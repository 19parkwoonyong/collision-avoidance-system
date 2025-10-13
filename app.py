# app.py — Flask API (로그인/회원가입 + 장치 상태/전원 + 센서 보고 수신)
# 실행: python app.py  (기본 포트 5000, 외부접속 가능)

from flask import Flask, request, jsonify, session
from flask_cors import CORS
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import datetime, os, subprocess, platform, shutil

app = Flask(__name__)

# ── CORS: 프론트 개발 주소를 명시 허용 (credentials 사용 시 * 불가)
CORS(app,
     supports_credentials=True,
     resources={r"/*": {"origins": [
         "http://localhost:3000",
         "http://127.0.0.1:3000",
         "http://your-ip-address:3000"
     ]}})

# DB & 세션
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'filesystem'
app.secret_key = 'esp32_secret'
Session(app)
db = SQLAlchemy(app)

# ---------------- 모델 ----------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    power = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(100), default="대기 중")
    last_report = db.Column(db.String(200), default="없음")
    last_updated = db.Column(db.String(100), default=None)
    signal_strength = db.Column(db.String(50), default="N/A")
    distance = db.Column(db.String(50), default="N/A")

# ---------------- 실행/중지 대상 (chair1) ----------------
# 라즈베리파이 SSH 설정
SSH_HOST = "your-ip-address"      # ← 라즈베리파이 IP/호스트명으로 변경
SSH_USER = "pi"                   # ← 사용자명
SSH_KEY  = None                   # 예: r"C:\Users\user\.ssh\id_ed25519" 또는 "/home/user/.ssh/id_rsa"
SSH_OPTS = "-o BatchMode=yes -o StrictHostKeyChecking=no"

# 라즈베리파이에서 실제로 돌릴 명령들
REMOTE = {
    "chair1": {
        "start": (
            "systemctl --user start chair1.service || "
            "sudo systemctl start chair1.service || "
            "nohup python3 /home/pi/chair/chair1.py "
            "> /home/pi/chair/chair1.log 2>&1 & echo $! > /home/pi/chair/chair1.pid"
        ),
        "stop": (
            "systemctl --user stop chair1.service || "
            "sudo systemctl stop chair1.service || "
            "(test -f /home/pi/chair/chair1.pid && kill $(cat /home/pi/chair/chair1.pid) && rm -f /home/pi/chair/chair1.pid || true)"
        ),
        "status": (
            "systemctl --user is-active chair1.service || "
            "systemctl is-active chair1.service || "
            "(test -f /home/pi/chair/chair1.pid && ps -p $(cat /home/pi/chair/chair1.pid) >/dev/null && echo active || echo inactive)"
        ),
    }
}

# 로컬에서 systemctl 가능 여부 감지 (리눅스 + systemctl 존재)
def _can_local_systemctl():
    return platform.system().lower() == "linux" and shutil.which("systemctl") is not None

def _run(cmd: str, timeout: int = 10):
    """로컬 쉘 실행 (타임아웃 포함)"""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "").strip() if e.stdout else "", "timeout"

def _ssh_cmd(remote_cmd: str) -> str:
    key_part = f"-i {SSH_KEY}" if SSH_KEY else ""
    # bash -lc 로 PATH/로그인 쉘 환경 문제 완화
    return f'ssh {SSH_OPTS} {key_part} {SSH_USER}@{SSH_HOST} "bash -lc \'{remote_cmd}\'"'

def trigger_process(device_name: str, turn_on: bool):
    """
    1) 만약 이 Flask가 리눅스 + systemctl 사용 가능하면 로컬에서 수행
    2) 아니면 SSH로 라즈베리파이에 접속해서 원격 실행
    """
    spec = REMOTE.get(device_name)
    if not spec:
        return False, f"'{device_name}'에 대한 REMOTE 매핑이 없습니다."

    action = "start" if turn_on else "stop"
    cmd_remote = spec[action]
    cmd_status = spec.get("status")

    # 우선순위 1: 로컬 systemctl (리눅스 서버일 때)
    if _can_local_systemctl():
        rc, out, err = _run(cmd_remote)
        ok = (rc == 0)
        status_msg = ""
        if cmd_status:
            src, sout, serr = _run(cmd_status)
            status_msg = f" / status={sout or serr or src}"
        return ok, f"local:{action} rc={rc} out={out} err={err}{status_msg}"

    # 우선순위 2: SSH로 원격 실행 (Windows/ macOS/ systemctl 없는 경우)
    ssh = _ssh_cmd(cmd_remote)
    rc, out, err = _run(ssh)
    ok = (rc == 0)
    status_msg = ""
    if cmd_status:
        s_rc, s_out, s_err = _run(_ssh_cmd(cmd_status))
        status_msg = f" / status={s_out or s_err or s_rc}"
    return ok, f"ssh:{action} rc={rc} out={out} err={err}{status_msg}"

# ---------------- 사용자 ----------------
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    username = data.get('username'); password = data.get('password')
    if not username or not password:
        return jsonify({"error": "아이디와 비밀번호를 모두 입력하세요."}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "이미 존재하는 사용자입니다."}), 409
    user = User(username=username, password_hash=generate_password_hash(password))
    db.session.add(user); db.session.commit()
    return jsonify({"message": "회원가입 성공"}), 201

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username'); password = data.get('password')
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        session['user'] = username
        return jsonify({"message": "Login successful"}), 200
    return jsonify({"error": "Invalid credentials"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return jsonify({"message": "Logged out successfully"}), 200

# ---------------- 장치 상태 ----------------
@app.route('/api/status', methods=['GET'])
def get_status():
    devices = Device.query.order_by(Device.id.asc()).all()
    return jsonify([
        {
            "id": d.id, "name": d.name, "power": d.power, "status": d.status,
            "last_report": d.last_report, "last_updated": d.last_updated,
            "signal_strength": d.signal_strength, "distance": d.distance
        } for d in devices
    ])

@app.route('/api/status/<name>', methods=['GET'])
def get_status_one(name):
    d = Device.query.filter_by(name=name).first()
    if not d:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": d.id, "name": d.name, "power": d.power, "status": d.status,
        "last_report": d.last_report, "last_updated": d.last_updated,
        "signal_strength": d.signal_strength, "distance": d.distance
    })

# ---------------- 전원(= 코드 실행/중지) ----------------
@app.route('/api/power', methods=['POST'])
def set_power():
    data = request.json or {}
    device_name = data.get("device") or data.get("deviceName")
    power_on = bool(data.get("on"))
    if not device_name:
        return jsonify({"error": "device name required"}), 400

    d = Device.query.filter_by(name=device_name).first()
    if not d:
        d = Device(name=device_name)
        db.session.add(d)

    ok, log = trigger_process(device_name, power_on)

    if ok:
        d.power = power_on
        d.status = "동작 중" if power_on else "대기 중"
    d.last_report = (f"프로세스 {'실행' if power_on else '중지'} 요청: "
                     f"{'성공' if ok else '실패'} / {log}")
    d.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.session.commit()

    if not ok:
        return jsonify({"error": "실행/중지 실패", "detail": log}), 500

    return jsonify({"message": "전원 상태 변경 완료",
                    "device": device_name, "power": d.power,
                    "detail": log}), 200

# (선택) 상태 확인
@app.route('/api/power/<name>/status', methods=['GET'])
def power_status(name):
    spec = REMOTE.get(name)
    if not spec or not spec.get("status"):
        return jsonify({"name": name, "status": "unknown"}), 200
    if _can_local_systemctl():
        rc, out, err = _run(spec["status"])
    else:
        rc, out, err = _run(_ssh_cmd(spec["status"]))
    return jsonify({"name": name, "status": (out or err or str(rc)).strip()}), 200

# ---------------- 센서 보고 수신 ----------------
@app.route("/api/device-report", methods=["POST"])
def report():
    data = request.json or {}
    device_name = data.get("device") or data.get("deviceName") or "unknown"
    message = data.get("message", "")
    signal = data.get("signal_strength", data.get("rssi", "N/A"))
    distance = data.get("distance", "N/A")

    d = Device.query.filter_by(name=device_name).first()
    if not d:
        d = Device(name=device_name)
        db.session.add(d)

    d.last_report = message
    d.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d.signal_strength = str(signal) if signal is not None else "N/A"
    d.distance = str(distance) if distance is not None else "N/A"
    db.session.commit()
    return jsonify({"received": True})

# (선택) 수동 시드
@app.route("/api/seed", methods=["POST"])
def seed():
    names = (request.json or {}).get("names", ["chair1"])
    created = []
    for n in names:
        if not Device.query.filter_by(name=n).first():
            db.session.add(Device(name=n)); created.append(n)
    db.session.commit()
    return jsonify({"created": created})

# (진단용) 헬스체크
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "devices": Device.query.count()})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # ── 서버 기동 시 장치가 없으면 기본 chair1 자동 생성
        if Device.query.count() == 0:
            db.session.add(Device(name="chair1"))
            db.session.commit()
            print("[INIT] seeded default device: chair1")
    app.run(host='0.0.0.0', port=5000, debug=True)
