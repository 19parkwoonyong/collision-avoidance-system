# app.py — Flask API (로그인/회원가입 + 장치 상태/전원 + 센서 보고 수신 + 에이전트 프록시)
# 실행: python app.py

from flask import Flask, request, jsonify, session
from flask_cors import CORS
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import datetime, os, subprocess, platform, shutil, sqlite3
import requests  # 프록시 호출용

app = Flask(__name__)

# ── CORS
CORS(app,
     supports_credentials=True,
     resources={r"/*": {"origins": [
         "http://localhost:3000",
         "http://127.0.0.1:3000",
         "http://your IP address"
     ]}})

# DB & 세션
basedir = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(basedir, 'users.db')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DB_PATH
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
    control_url = db.Column(db.String(200), default=None)  # 에이전트 제어 URL

# ---- (마이그레이션 보정) control_url 컬럼이 없으면 추가 ----
def ensure_control_url_column():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute("PRAGMA table_info(Device)")
            cols = [r[1] for r in cur.fetchall()]
            if "control_url" not in cols:
                conn.execute("ALTER TABLE Device ADD COLUMN control_url TEXT")
                conn.commit()
                print("[DB] Added column control_url to Device")
    except Exception as e:
        print("[DB] Column check/add failed:", e)

# ---------------- SSH 폴백(기존 방식 유지) ----------------
SSH_HOST = "your IP address"   # 필요시 수정
SSH_USER = "pi"
SSH_KEY  = None
SSH_OPTS = "-o BatchMode=yes -o StrictHostKeyChecking=no"

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

def _can_local_systemctl():
    return platform.system().lower() == "linux" and shutil.which("systemctl") is not None

def _run(cmd: str, timeout: int = 10):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "").strip() if e.stdout else "", "timeout"

def _ssh_cmd(remote_cmd: str) -> str:
    key_part = f"-i {SSH_KEY}" if SSH_KEY else ""
    return f'ssh {SSH_OPTS} {key_part} {SSH_USER}@{SSH_HOST} "bash -lc \'{remote_cmd}\'"'

# ★ 빠졌던 함수 복구: SSH/로컬 systemctl로 프로세스 시작/중지
def trigger_process(device_name: str, turn_on: bool):
    """
    1) 서버가 리눅스 + systemctl 가능 → 로컬에서 실행
    2) 아니면 SSH로 원격 실행
    """
    spec = REMOTE.get(device_name)
    if not spec:
        return False, f"'{device_name}'에 대한 REMOTE 매핑이 없습니다."

    action = "start" if turn_on else "stop"
    cmd_remote = spec[action]
    cmd_status = spec.get("status")

    # 우선순위 1: 로컬
    if _can_local_systemctl():
        rc, out, err = _run(cmd_remote)
        ok = (rc == 0)
        status_msg = ""
        if cmd_status:
            src, sout, serr = _run(cmd_status)
            status_msg = f" / status={sout or serr or src}"
        return ok, f"local:{action} rc={rc} out={out} err={err}{status_msg}"

    # 우선순위 2: SSH
    ssh = _ssh_cmd(cmd_remote)
    rc, out, err = _run(ssh)
    ok = (rc == 0)
    status_msg = ""
    if cmd_status:
        s_rc, s_out, s_err = _run(_ssh_cmd(cmd_status))
        status_msg = f" / status={s_out or s_err or s_rc}"
    return ok, f"ssh:{action} rc={rc} out={out} err={err}{status_msg}"

# ---------------- 에이전트 프록시 ----------------
AGENT_TIMEOUT = 2.5

def _agent_post(d: Device, path: str):
    if not d or not d.control_url:
        return False, "control_url 없음"
    try:
        r = requests.post(f"{d.control_url}{path}", timeout=AGENT_TIMEOUT)
        if r.status_code == 200:
            return True, r.text
        return False, f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)

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
            "signal_strength": d.signal_strength, "distance": d.distance,
            "control_url": d.control_url
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
        "signal_strength": d.signal_strength, "distance": d.distance,
        "control_url": d.control_url
    })

# ---------------- 에이전트 제어(프록시) ----------------
@app.route('/api/agent/<name>/wake', methods=['POST'])
def agent_wake(name):
    d = Device.query.filter_by(name=name).first()
    if not d: return jsonify({"error": "not found"}), 404
    ok, detail = _agent_post(d, "/wake")
    d.last_report = f"에이전트 WAKE 요청: {'성공' if ok else '실패'} / {detail}"
    d.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if ok: d.power, d.status = True, "동작 중"
    db.session.commit()
    return (jsonify({"ok": True, "detail": detail}), 200) if ok else (jsonify({"error":"실행 실패","detail":detail}), 500)

@app.route('/api/agent/<name>/sleep', methods=['POST'])
def agent_sleep(name):
    d = Device.query.filter_by(name=name).first()
    if not d: return jsonify({"error": "not found"}), 404
    ok, detail = _agent_post(d, "/sleep")
    d.last_report = f"에이전트 SLEEP 요청: {'성공' if ok else '실패'} / {detail}"
    d.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if ok: d.power, d.status = False, "대기 중"
    db.session.commit()
    return (jsonify({"ok": True, "detail": detail}), 200) if ok else (jsonify({"error":"중지 실패","detail":detail}), 500)

@app.route('/api/agent/<name>/quit', methods=['POST'])
def agent_quit(name):
    d = Device.query.filter_by(name=name).first()
    if not d: return jsonify({"error": "not found"}), 404
    ok, detail = _agent_post(d, "/quit")
    d.last_report = f"에이전트 QUIT 요청: {'성공' if ok else '실패'} / {detail}"
    d.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if ok: d.power, d.status = False, "대기 중"
    db.session.commit()
    return (jsonify({"ok": True, "detail": detail}), 200) if ok else (jsonify({"error":"종료 실패","detail":detail}), 500)

@app.route('/api/agent/<name>/health', methods=['GET'])
def agent_health(name):
    d = Device.query.filter_by(name=name).first()
    if not d or not d.control_url:
        return jsonify({"error": "not found or no control_url"}), 404
    try:
        r = requests.get(f"{d.control_url}/health", timeout=AGENT_TIMEOUT)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ---------------- (옵션) 기존 SSH 경로 유지 ----------------
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

    # 1순위: control_url 있으면 프록시 사용
    if d.control_url:
        path = "/wake" if power_on else "/sleep"
        ok, detail = _agent_post(d, path)
        if ok:
            d.power = power_on
            d.status = "동작 중" if power_on else "대기 중"
        d.last_report = f"프록시 전원 요청: {'성공' if ok else '실패'} / {detail}"
        d.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.session.commit()
        return (jsonify({"message":"전원 상태 변경","detail":detail}), 200) if ok else (jsonify({"error":"실행/중지 실패","detail":detail}), 500)

    # 2순위: SSH 폴백
    ok, log = trigger_process(device_name, power_on)  # ← 이제 정의됨
    if ok:
        d.power = power_on
        d.status = "동작 중" if power_on else "대기 중"
    d.last_report = (f"SSH 전원 요청: {'성공' if ok else '실패'} / {log}")
    d.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.session.commit()
    return (jsonify({"message": "전원 상태 변경 완료", "detail": log}), 200) if ok else (jsonify({"error":"실행/중지 실패","detail":log}), 500)

# ---------------- 센서 보고 수신 ----------------
@app.route("/api/device-report", methods=["POST"])
def report():
    data = request.json or {}
    device_name = data.get("device") or data.get("deviceName") or "unknown"
    message = data.get("message", "")
    signal = data.get("signal_strength", data.get("rssi", "N/A"))
    distance = data.get("distance", "N/A")
    control_url = data.get("control_url")

    d = Device.query.filter_by(name=device_name).first()
    if not d:
        d = Device(name=device_name)
        db.session.add(d)

    d.last_report = message
    d.last_updated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d.signal_strength = str(signal) if signal is not None else "N/A"
    d.distance = str(distance) if distance is not None else "N/A"
    if control_url:
        d.control_url = control_url
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

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "devices": Device.query.count()})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        ensure_control_url_column()  # 컬럼 보정
        if Device.query.count() == 0:
            db.session.add(Device(name="chair1"))
            db.session.commit()
            print("[INIT] seeded default device: chair1")
    app.run(host='0.0.0.0', port=5000, debug=True)

