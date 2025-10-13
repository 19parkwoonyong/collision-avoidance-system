🧭 Smart Chair System 실행 가이드 (Flask + React + Raspberry Pi)

이 문서는 이 프로젝트를 처음 받아서 바로 실행 가능한 환경 구성 및 모듈 설치 방법을 설명합니다.
IP 주소 등 개인 환경에 맞는 부분은 .env 또는 코드 주석의 "your-ip-address"에 직접 입력하세요.

🪑 구성 요약
| 구성 요소            | 역할                                       | 주요 파일                 |
| ---------------- | ---------------------------------------- | --------------------- |
| **Flask 백엔드**    | 로그인 / 회원관리 / 장치 상태 관리 / 센서 데이터 수신 / 원격제어 | `backend/app.py`      |
| **React 프론트엔드**  | 관리자 대시보드 (로그인, 장치 상태 실시간 표시)             | `frontend/src/App.js` |
| **Raspberry Pi** | PIR + 초음파 센서 데이터 감지 후 Flask 서버로 보고       | `raspberry/chair1.py` |
