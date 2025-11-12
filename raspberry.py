#!/usr/bin/env python3
# Raspberry Pi agent (PIR + HC-SR04 + 3xLED + Active Buzzer + server reporting) with built-in REST control
# + Fast ultrasonic tracking (0.1s) + Anti-flicker LED latch + Buzzer PWM volume control
# sudo apt -y install python3-rpi.gpio python3-requests wireless-tools iw

import sys, time, argparse, statistics, subprocess, threading, socket, json
import datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
import RPi.GPIO as GPIO

# ---------- 기본값 ----------
DEFAULT_SERVER   = "http://'your server IP address':5000"
DEFAULT_DEVICE   = "chair1"

# 핀(BCM)
DEF_PIR   = 17
DEF_TRIG  = 23
DEF_ECHO  = 24   # ★ 5V→3.3V 레벨다운 필수
DEF_LED1  = 25
DEF_LED2  = 22
DEF_LED3  = 27
DEF_BUZZER= 18   # 능동부저(Active buzzer) — 단순 HIGH=ON, LOW=OFF

# ---------- 동작 파라미터 ----------
DISTANCE_THRESHOLD_CM      = 130.0
MEASUREMENT_INTERVAL_MS    = 3000   # 주기측정
COOLDOWN_MS                = 3000
POWER_POLL_MS              = 3000
REPORT_MIN_INTERVAL_MS     = 1000
WARMUP_SECONDS_DEFAULT     = 45
ULTRA_TIMEOUT_S            = 0.04
ULTRA_SAMPLES              = 3
DIST_MIN_CM, DIST_MAX_CM   = 2.0, 400.0
HTTP_TIMEOUT               = 2.5

# 빠른 추적(즉각 대응)
FAST_TRACK_INTERVAL_MS     = 100    # 0.1s
REARM_MODE                 = "cooldown"  # "edge" or "cooldown"

# --- Anti-flicker LED 제어 ---
LED_MIN_ON_MS              = 250
LED_MIN_OFF_MS             = 100
CONSEC_CLOSE_REQUIRED      = 2

# --- Buzzer PWM(음량 조절) ---
USE_BUZZER_PWM   = True     # True면 PWM으로 평균출력 낮춰 음량 감소
BUZZER_PWM_FREQ  = 5000     # 2 kHz 게이팅 (필요시 1500~4000 튜닝)
BUZZER_PWM_DUTY  = 30       # % (작을수록 더 조용함. 예: 10~30)

# ---------- util ----------
try: sys.stdout.reconfigure(line_buffering=True)
except Exception: pass
log = lambda *a, **k: print(*a, **k, flush=True)
def now_ms() -> int: return int(time.monotonic() * 1000)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ---------- RSSI ----------
def read_rssi():
    try:
        cp = subprocess.run(["iwconfig"], capture_output=True, text=True)
        out = (cp.stdout or "") + (cp.stderr or "")
        for line in out.splitlines():
            if "Signal level" in line:
                for tok in line.split():
                    if tok.startswith("level=") and "dBm" in tok:
                        return int(tok.split("=")[1].replace("dBm",""))
    except Exception:
        pass
    try:
        cp = subprocess.run(["iw", "dev", "wlan0", "link"], capture_output=True, text=True)
        for line in (cp.stdout or "").splitlines():
            if "signal:" in line:
                return int(line.split(":")[1].strip().split()[0])
    except Exception:
        pass
    return None

# ---------- 서버 통신 ----------
def report(base, device, message, distance=None, control_url=None):
    url = f"{base}/api/device-report"
    url = f"{base}/api/device-report"
    payload = {
        "device": device,
        "message": message,
        "distance": float(distance) if distance is not None else None,
        "signal_strength": read_rssi()
    }
    if control_url: payload["control_url"] = control_url
    try:
        requests.post(url, json=payload, timeout=HTTP_TIMEOUT)
        return True
    except Exception:
        return False

def get_power_flag(base, device, default=True) -> bool:
    url = f"{base}/api/status/{device}"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return bool(r.json().get("power", default))
    except Exception:
        pass
    return default

# ---------- GPIO ----------
def setup_gpio(pir, trig, echo, led_pins, buzzer, pud_mode: str):
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    if pud_mode == "up":
        GPIO.setup(pir, GPIO.IN, pull_up_down=GPIO.PUD_UP); used = "PUD_UP"
    elif pud_mode == "down":
        GPIO.setup(pir, GPIO.IN, pull_up_down=GPIO.PUD_DOWN); used = "PUD_DOWN"
    else:
        GPIO.setup(pir, GPIO.IN, pull_up_down=GPIO.PUD_DOWN); used = "PUD_DOWN"
    GPIO.setup(trig, GPIO.OUT); GPIO.output(trig, GPIO.LOW)
    GPIO.setup(echo, GPIO.IN)
    for pin in led_pins:
        GPIO.setup(pin, GPIO.OUT); GPIO.output(pin, GPIO.LOW)
    # buzzer (능동부저: ON/OFF or PWM 게이팅)
    GPIO.setup(buzzer, GPIO.OUT); GPIO.output(buzzer, GPIO.LOW)
    return used

def maybe_switch_pud_auto(pir, current_used: str) -> str:
    if current_used != "PUD_DOWN": return current_used
    hi = False; t0 = time.monotonic()
    while time.monotonic() - t0 < 1.0:
        hi |= GPIO.input(pir) == 1
        time.sleep(0.05)
    if not hi:
        GPIO.setup(pir, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        return "PUD_UP"
    return current_used

def leds_hw_set(led_pins, on: bool):
    lvl = GPIO.HIGH if on else GPIO.LOW
    for p in led_pins: GPIO.output(p, lvl)

# --- Buzzer: PWM/ON-OFF 공용 제어 ---
buz_pwm = None  # PWM 핸들(전역)

def buzzer_hw_set(pin, on: bool):
    global buz_pwm
    if USE_BUZZER_PWM:
        if on:
            if buz_pwm is not None:
                try:
                    buz_pwm.start(BUZZER_PWM_DUTY)  # 평균출력 ↓ → 음량 ↓
                except RuntimeError:
                    buz_pwm.ChangeDutyCycle(BUZZER_PWM_DUTY)
        else:
            if buz_pwm is not None:
                try:
                    buz_pwm.stop()
                except Exception:
                    pass
            GPIO.output(pin, GPIO.LOW)
    else:
        GPIO.output(pin, GPIO.HIGH if on else GPIO.LOW)

# ---------- 초음파 ----------
def measure_once_cm(trig, echo, timeout_s=ULTRA_TIMEOUT_S):
    GPIO.output(trig, GPIO.LOW); time.sleep(2e-6)
    GPIO.output(trig, GPIO.HIGH); time.sleep(10e-6)
    GPIO.output(trig, GPIO.LOW)

    start = time.monotonic()
    while GPIO.input(echo) == 0:
        if time.monotonic() - start > timeout_s:
            return None, "ECHO_LOW_TIMEOUT"
    t0 = time.monotonic()
    while GPIO.input(echo) == 1:
        if time.monotonic() - t0 > timeout_s:
            return None, "ECHO_HIGH_TIMEOUT"
    t1 = time.monotonic()

    pulse = t1 - t0
    dist = (pulse * 34300.0) / 2.0
    return round(dist, 1), None

def measure_median_cm(trig, echo, n=ULTRA_SAMPLES):
    samples = []; last_err = None
    for _ in range(n):
        d, err = measure_once_cm(trig, echo)
        last_err = err or last_err
        if d is not None and DIST_MIN_CM <= d <= DIST_MAX_CM:
            samples.append(d)
        time.sleep(0.01)
    if not samples:
        return None, last_err
    return round(statistics.median(samples), 1), last_err

# ---------- 전역 상태 ----------
SYSTEM_ACTIVE      = True
SHUTDOWN_REQUESTED = False

# ---------- 내장 HTTP 제어 서버 ----------
class CtlHandler(BaseHTTPRequestHandler):
    def _ok(self, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def _err(self, code, msg):
        data = json.dumps({"error": msg}).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a): return
    def do_GET(self):
        if self.path == "/health": self._ok({"running": True, "active": SYSTEM_ACTIVE})
        else: self._err(404, "not found")
    def do_POST(self):
        global SYSTEM_ACTIVE, SHUTDOWN_REQUESTED
        if   self.path == "/wake":  SYSTEM_ACTIVE = True;  self._ok({"ok": True, "active": SYSTEM_ACTIVE})
        elif self.path == "/sleep": SYSTEM_ACTIVE = False; self._ok({"ok": True, "active": SYSTEM_ACTIVE})
        elif self.path == "/quit":  SHUTDOWN_REQUESTED = True; self._ok({"ok": True, "quitting": True})
        else: self._err(404, "not found")

def run_ctl_server(host, port):
    httpd = HTTPServer((host, port), CtlHandler)
    httpd.serve_forever()

# ---------- 메인 ----------
def main():
    parser = argparse.ArgumentParser(description="Pi agent + fast ultrasonic tracking + anti-flicker LED + buzzer PWM volume control")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--pir",   type=int, default=DEF_PIR)
    parser.add_argument("--trig",  type=int, default=DEF_TRIG)
    parser.add_argument("--echo",  type=int, default=DEF_ECHO)
    parser.add_argument("--led1",  type=int, default=DEF_LED1)
    parser.add_argument("--led2",  type=int, default=DEF_LED2)
    parser.add_argument("--led3",  type=int, default=DEF_LED3)
    parser.add_argument("--buzzer",type=int, default=DEF_BUZZER)
    parser.add_argument("--pud",   choices=["auto","up","down"], default="auto")
    parser.add_argument("--warmup",type=int, default=WARMUP_SECONDS_DEFAULT)
    parser.add_argument("--ctl-port", type=int, default=5050)
    args = parser.parse_args()

    led_pins = [args.led1, args.led2, args.led3]
    buz_pin  = args.buzzer

    # 제어 서버
    local_ip = get_local_ip()
    ctl_url = f"http://{local_ip}:{args.ctl_port}"
    threading.Thread(target=run_ctl_server, args=("0.0.0.0", args.ctl_port), daemon=True).start()

    # GPIO
    used_pud = setup_gpio(args.pir, args.trig, args.echo, led_pins, buz_pin, args.pud)
    log(f"[START] {dt.datetime.now():%F %T} server={args.server} device={args.device}")
    log(f"pins(BCM) PIR:{args.pir} TRIG:{args.trig} ECHO:{args.echo} LEDS:{led_pins} BUZZER:{buz_pin} PUD={used_pud}")
    log(f"control_url={ctl_url}")
    report(args.server, args.device, "센서 클라이언트 기동 (fast+anti-flicker+buzzer-PWM)", control_url=ctl_url)

    # PWM 준비 (시작은 OFF)
    global buz_pwm
    if USE_BUZZER_PWM:
        buz_pwm = GPIO.PWM(buz_pin, BUZZER_PWM_FREQ)

    # PUD auto
    if args.pud == "auto":
        used_pud = maybe_switch_pud_auto(args.pir, used_pud)
        if used_pud == "PUD_UP": log("PIR 입력 모드 자동 전환: PUD_UP")

    # PIR 워밍업
    if args.warmup > 0:
        log(f"PIR 워밍업 중... {args.warmup}s 대기")
        end = time.monotonic() + args.warmup; next_tick = 0
        while time.monotonic() < end:
            if time.monotonic() >= next_tick:
                remain = int(end - time.monotonic())
                log(f"  ...{remain}s 남음 (PIR={GPIO.input(args.pir)})")
                next_tick = time.monotonic() + 1
            time.sleep(0.05)
        log("✅ PIR 센서 준비 완료")

    # 상태 공유
    global SYSTEM_ACTIVE, SHUTDOWN_REQUESTED
    SYSTEM_ACTIVE      = True
    SHUTDOWN_REQUESTED = False
    state = {
        "in_cooldown": False,
        "cooldown_until": 0,
        "last_measure": 0,
        "last_power_poll": 0,
        "last_report": 0,
        "last_idle_log": 0,
        "armed": True,
        # LED 제어(래치 + 최소 유지)
        "led_desired": False,
        "led_actual":  False,
        "last_led_change": 0,
    }

    lock = threading.Lock()
    fast_track_enable = threading.Event()
    stop_event        = threading.Event()

    # ---- LED/Buzzer 제어(래치) ----
    def led_request(on: bool):
        with lock:
            state["led_desired"] = bool(on)

    def led_manager():
        # 최소 유지시간 보장 + 단일 적용 지점
        with lock:
            desired = state["led_desired"]
            actual  = state["led_actual"]
            lastchg = state["last_led_change"]
        now = now_ms()

        if desired != actual:
            if (not actual) and (now - lastchg < LED_MIN_OFF_MS):
                return
            if actual and (now - lastchg < LED_MIN_ON_MS):
                return
            # 실제 하드웨어 적용: LED들과 부저를 동일 상태로 동기화
            leds_hw_set(led_pins, desired)
            buzzer_hw_set(buz_pin, desired)   # ★ LED와 동시 ON/OFF (PWM으로 음량 제어)
            with lock:
                state["led_actual"] = desired
                state["last_led_change"] = now

    # ---------- 빠른 추적 스레드 ----------
    def fast_tracker():
        consec_close = 0
        while not stop_event.is_set():
            if not fast_track_enable.is_set():
                consec_close = 0
                time.sleep(0.05)
                continue

            cur_pir = GPIO.input(args.pir)
            with lock:
                active   = SYSTEM_ACTIVE
                cool     = state["in_cooldown"]
                armed    = state["armed"]

            if active and (not cool) and cur_pir == 1 and (REARM_MODE == "cooldown" or armed):
                d, err = measure_once_cm(args.trig, args.echo)
                if d is None:
                    consec_close = 0
                    with lock:
                        if now_ms() - state["last_report"] >= REPORT_MIN_INTERVAL_MS:
                            report(args.server, args.device, "초음파 응답 없음"); state["last_report"] = now_ms()
                else:
                    log(f"[FAST] distance={d} cm")
                    if d <= DISTANCE_THRESHOLD_CM:
                        consec_close += 1
                    else:
                        consec_close = 0

                    if consec_close >= CONSEC_CLOSE_REQUIRED:
                        led_request(True)
                        with lock:
                            state["in_cooldown"]    = True
                            state["cooldown_until"] = now_ms() + COOLDOWN_MS
                            if REARM_MODE == "edge":
                                state["armed"] = False
                            if now_ms() - state["last_report"] >= REPORT_MIN_INTERVAL_MS:
                                report(args.server, args.device, "사람 감지 및 LED/BUZZER 점등(FAST)", distance=d)
                                state["last_report"] = now_ms()
                        consec_close = 0
            time.sleep(FAST_TRACK_INTERVAL_MS / 1000.0)

    th = threading.Thread(target=fast_tracker, daemon=True); th.start()

    prev_pir = GPIO.input(args.pir)

    try:
        while not SHUTDOWN_REQUESTED:
            now = now_ms()

            # 서버 power flag
            if now - state["last_power_poll"] >= POWER_POLL_MS:
                state["last_power_poll"] = now
                new_flag = get_power_flag(args.server, args.device, default=SYSTEM_ACTIVE)
                if new_flag != SYSTEM_ACTIVE:
                    SYSTEM_ACTIVE = new_flag; log(f"POWER FLAG -> {SYSTEM_ACTIVE}")

            cur_pir = GPIO.input(args.pir)
            if cur_pir != prev_pir:
                log("PIR:", "사람 감지됨" if cur_pir else "움직임 없음")
                prev_pir = cur_pir
                if cur_pir == 0 and now - state["last_report"] >= REPORT_MIN_INTERVAL_MS:
                    report(args.server, args.device, "PIR 미감지"); state["last_report"] = now
                if cur_pir == 0:
                    with lock: state["armed"] = True  # 재무장

            # 하트비트
            if now - state["last_idle_log"] >= 1000:
                state["last_idle_log"] = now
                leds_state = "".join("1" if GPIO.input(p) else "0" for p in led_pins)
                buz_state  = "1" if GPIO.input(buz_pin) else "0"
                with lock:
                    hb = f"[HB] active={SYSTEM_ACTIVE} PIR={'HIGH' if cur_pir else 'LOW '} LEDS={leds_state} BUZ={buz_state} cooldown={state['in_cooldown']} armed={state['armed']}"
                log(hb)

            if not SYSTEM_ACTIVE:
                fast_track_enable.clear()
                led_manager()
                time.sleep(0.2)
                continue

            # 쿨다운 관리
            with lock:
                in_cd   = state["in_cooldown"]
                cd_end  = state["cooldown_until"]

            if in_cd and now >= cd_end:
                with lock:
                    state["in_cooldown"] = False
                # OFF는 여기서만 수행(안티-플리커 정책 유지)
                led_request(False)
                log("쿨다운 종료 → LEDs/Buzzer OFF")
                if REARM_MODE == "cooldown":
                    if GPIO.input(args.pir) == 1:
                        with lock: state["armed"] = True

            # 빠른 추적 활성 조건
            if SYSTEM_ACTIVE and (not in_cd) and cur_pir == 1:
                fast_track_enable.set()
            else:
                fast_track_enable.clear()

            # 보강용 주기 측정
            if (now - state["last_measure"]) >= MEASUREMENT_INTERVAL_MS and not in_cd:
                state["last_measure"] = now
                if cur_pir == 1:
                    d, err = measure_median_cm(args.trig, args.echo, n=ULTRA_SAMPLES)
                    if d is None:
                        if now - state["last_report"] >= REPORT_MIN_INTERVAL_MS:
                            report(args.server, args.device, "초음파 응답 없음"); state["last_report"] = now
                    else:
                        log(f"Measured distance (median {ULTRA_SAMPLES}): {d} cm")
                        if d <= DISTANCE_THRESHOLD_CM:
                            led_request(True)
                            with lock:
                                state["in_cooldown"]    = True
                                state["cooldown_until"] = now + COOLDOWN_MS
                                if REARM_MODE == "edge":
                                    state["armed"] = False
                            if now - state["last_report"] >= REPORT_MIN_INTERVAL_MS:
                                report(args.server, args.device, "사람 감지 및 LED/BUZZER 점등(PERIODIC)", distance=d)
                                state["last_report"] = now
                        else:
                            if now - state["last_report"] >= REPORT_MIN_INTERVAL_MS:
                                report(args.server, args.device, "거리 초과, 감지 무효", distance=d)
                                state["last_report"] = now
                else:
                    if now - state["last_report"] >= REPORT_MIN_INTERVAL_MS:
                        report(args.server, args.device, "PIR 미감지"); state["last_report"] = now

            # 실제 적용
            led_manager()
            time.sleep(0.003)

    except KeyboardInterrupt:
        pass
    finally:
        try:
            if buz_pwm is not None:
                buz_pwm.stop()
        except Exception:
            pass
        GPIO.output(buz_pin, GPIO.LOW)
        for p in led_pins: GPIO.output(p, GPIO.LOW)
        GPIO.cleanup()
        report(args.server, args.device, "센서 클라이언트 종료")
        log(f"[STOP] {dt.datetime.now():%F %T}")

if __name__ == "__main__":
    main()
