🧭 Smart Chair System 실행 가이드 (Flask + React + Raspberry Pi)

이 문서는 이 프로젝트를 처음 받아서 바로 실행 가능한 환경 구성 및 모듈 설치 방법을 설명합니다.
IP 주소 등 개인 환경에 맞는 부분은 .env 또는 코드 주석의 "your-ip-address"에 직접 입력하세요.

🪑 구성 요약
| 구성 요소            | 역할                                       | 주요 파일                 |
| ---------------- | ---------------------------------------- | --------------------- |
| **Flask 백엔드**    | 로그인 / 회원관리 / 장치 상태 관리 / 센서 데이터 수신 / 원격제어 | `backend/app.py`      |
| **React 프론트엔드**  | 관리자 대시보드 (로그인, 장치 상태 실시간 표시)             | `frontend/src/App.js` |
| **Raspberry Pi** | PIR + 초음파 센서 데이터 감지 후 Flask 서버로 보고       | `raspberry/chair1.py` |

###⚙️ 1. 환경 준비
✅ Python 3.9 이상

Flask 서버용 (Windows, macOS, Linux 모두 가능)

✅ Node.js 18 이상

React 프론트엔드용 (권장 Node 20+)

✅ Raspberry Pi 4 (권장)

센서 제어용 (PIR + HC-SR04P + LED)


###🧩 2. Python (Flask 서버) 설정
📁 이동
cd backend

📦 가상환경 생성 및 활성화
Windows

python -m venv .venv
.venv\Scripts\activate

macOS / Linux

python3 -m venv .venv
source .venv/bin/activate

📚 필수 모듈 설치
pip install -r requirements.txt

⚙️ .env 환경파일 설정

.env.example을 복사해 .env로 이름 변경 후 IP, SSH 등 필요한 부분 수정:
예시:

FLASK_SECRET=your_secret_key
FLASK_PORT=5000
ALLOWED_ORIGINS=http://localhost:3000
SSH_HOST=your-ip-address
SSH_USER=pi
SSH_KEY=/home/user/.ssh/id_rsa

▶️ Flask 실행
python app.py


정상 출력 예:

[INIT] seeded default device: chair1
 * Running on http://0.0.0.0:5000 (Press CTRL+C to quit)


이제 Flask 서버가 5000번 포트에서 API 요청을 받을 준비가 됨.
헬스체크: http://localhost:5000/api/health


💻 3. React (프론트엔드) 설정
📁 이동
cd frontend

📦 모듈 설치
npm install


자동 설치되는 주요 라이브러리

react, axios, react-dom, react-scripts

⚙️ 환경설정

.env.example을 복사해 .env로 변경:

cp .env.example .env


필요 시 Flask 서버 주소 지정:

REACT_APP_API=http://your-ip-address:5000


동일 PC에서 개발 중이라면 이 설정이 없어도 됨
(App.js가 window.location.hostname을 자동 인식함)

▶️ React 실행
npm start


브라우저 자동 실행 → http://localhost:3000

첫 화면: 로그인 / 회원가입

로그인 후: 장치 카드 목록

클릭: 상세정보 및 코드 실행/중지 버튼


🧠 4. Raspberry Pi (센서 코드)
📁 코드 구조

예: raspberry/chair1.py

PIR 센서 (GPIO 17)

초음파 센서 (TRIG: GPIO 23 / ECHO: GPIO 24)

LED (GPIO 25)

Flask 서버로 주기적 보고 (POST /api/device-report)

⚙️ 필요한 모듈 설치

라즈베리파이 터미널에서:

sudo apt update
sudo apt install python3-rpi.gpio python3-requests -y

▶️ 센서 코드 실행
python3 chair1.py


Flask 서버 콘솔에 다음과 같은 로그가 찍히면 성공:

POST /api/device-report 200 OK


React 대시보드에서도 상태가 자동 갱신됨 ✅


🔌 5. 실행 순서 요약
| 단계  | 위치           | 명령                  |
| --- | ------------ | ------------------- |
| 1️⃣ | `backend/`   | `python app.py`     |
| 2️⃣ | `frontend/`  | `npm start`         |
| 3️⃣ | `raspberry/` | `python3 chair1.py` |



🔍 6. 기본 점검 포인트
| 항목       | 확인 방법                              | 기대 결과            |
| -------- | ---------------------------------- | ---------------- |
| Flask 서버 | `http://localhost:5000/api/health` | `{ "ok": true }` |
| 프론트 연결   | `http://localhost:3000`            | 로그인 화면 표시        |
| 센서 보고    | Flask 터미널 출력                       | `received: True` |
| 대시보드 갱신  | React 상세 화면                        | 거리/시간/상태 갱신됨     |


🧰 7. 수동 명령 예시
장치 시드 (DB 초기화)
curl -X POST http://localhost:5000/api/seed -H "Content-Type: application/json" -d '{"names":["chair1"]}'

센서 테스트 보고
curl -X POST http://localhost:5000/api/device-report -H "Content-Type: application/json" -d '{"device":"chair1","message":"테스트 보고","signal_strength":"-60","distance":"45"}'


⚠️ 8. 주의사항
구분	주의 내용
.env	절대 깃허브에 올리지 말 것
users.db	실제 사용자 정보 저장 → 업로드 금지
포트 충돌	Flask(5000), React(3000) 동시에 사용
SSH 원격실행	SSH 비활성화 시 Flask는 로컬만 동작


🧾 9. requirements.txt 내용 (참고)

만약 직접 만들어야 한다면:

Flask==3.0.3
Flask-Cors==4.0.1
Flask-Session==0.5.0
Flask-SQLAlchemy==3.1.1
Werkzeug==3.0.3
requests==2.31.0


10. 시스템 간략 요약도
<img width="944" height="229" alt="image" src="https://github.com/user-attachments/assets/dd478850-8ee5-4108-8401-c7a2fbc46900" />









