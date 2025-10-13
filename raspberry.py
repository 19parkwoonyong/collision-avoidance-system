#!/usr/bin/env python3
# Raspberry Pi TDM agent — PIR HIGH 동안 0.2s 간격 초음파 추적, 임계 도달 시 LED 점등 후 대기/재개
# sudo apt -y install python3-rpi.gpio python3-requests wireless-tools iw

import sys, time, argparse, statistics, subprocess
import datetime as dt
import requests
import RPi.GPIO as GPIO

# ---------- 기본값 ----------
DEFAULT_SERVER   = "http://'your IP adrress':5000"
DEFAULT_DEVICE   = "chair1"

# 핀은 BCM 번호
DEF_PIR  = 17
DEF_TRIG = 23
DEF_ECHO = 24   # ★ 5V→3.3V 레벨다운 필수
DEF_LED  = 25

# 동작 파라미터
DISTANCE_THRESHOLD_CM    = 150.0     # 임계 거리
COOLDOWN_MS              = 3000      # LED ON 유지 시간
POWER_POLL_MS            = 3000
REPORT_MIN_INTERVAL_MS   = 1000
WARMUP_SECONDS_DEFAULT   = 45
ULTRA_TIMEOUT_S          = 0.04      # 에지 대기 타임아웃
DIST_MIN_CM, DIST_MAX_CM = 2.0, 400.0
HTTP_TIMEOUT             = 2.5

# ---------- 재무장(재시작) 정책 ----------
# - "edge": PIR이 LOW→HIGH 에지가 생겨야만 재무장(기존 설계와 유사)
# - "cooldown": 쿨다운 종료 직후, PIR이 HIGH면 즉시 재무장+추적 재개
REARM_MODE = "cooldown"

# ---------- TDM (0.2초 간격 보장) ----------
SLOT_MS   = 50             # 1 슬롯 = 50ms
NUM_SLOTS = 4              # 4 슬롯 = 200ms(0.2s) 슈퍼프레임

# 슬롯 배치
SLOT_TASKS = {
    0: "POWER_POLL",     # 3초 스로틀
    1: "PIR_SAMPLE",     # PIR 상태 및 에지 감지, 무장(armed) 관리
    2: "ULTRA_TRACK",    # ★ 0.2초 간격 초음파 추적
    3: "LED_HB",         # LED 쿨다운 관리 + 하트비트
}

# ---------- 유틸 ----------
try: sys.stdout.reconfigure(line_buffering=True)
except Exception: pass
log = lambda *a, **k: print(*a, **k, flush=True)
def now_ms(): return int(time.monotonic() * 1000)
def ms_since(ts): return now_ms() - ts

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

# ---------- 서버 ----------
def report(base, device, message, distance=None):
    url = f"{base}/api/device-report"
    payload = {
        "device": device,
        "message": message,
        "distance": float(distance) if distance is not None else None,
        "signal_strength": read_rssi()
    }
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
def setup_gpio(pir, trig, echo, led, pud_mode: str):
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
    GPIO.setup(led,  GPIO.OUT); GPIO.output(led, GPIO.LOW)
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

def led_set(pin, on: bool):
    GPIO.output(pin, GPIO.HIGH if on else GPIO.LOW)

# ---------- 초음파 ----------
def measure_once_cm(trig, echo, timeout_s=ULTRA_TIMEOUT_S):
    # 1회 측정 (고속 추적용: 중앙값 없이 단일 샘플)
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

# ---------- 메인 ----------
def main():
    parser = argparse.ArgumentParser(description="Pi agent: 0.2s ultrasonic tracking while PIR=HIGH (rearm policies)")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--pir",  type=int, default=DEF_PIR)
    parser.add_argument("--trig", type=int, default=DEF_TRIG)
    parser.add_argument("--echo", type=int, default=DEF_ECHO)
    parser.add_argument("--led",  type=int, default=DEF_LED)
    parser.add_argument("--pud",  choices=["auto","up","down"], default="auto", help="PIR pull mode")
    parser.add_argument("--warmup", type=int, default=WARMUP_SECONDS_DEFAULT, help="PIR warmup seconds")
    # 고급: 슬롯 튜닝 (기본 50ms×4=200ms)
    parser.add_argument("--slot_ms", type=int, default=SLOT_MS)
    parser.add_argument("--num_slots", type=int, default=NUM_SLOTS)
    args = parser.parse_args()

    used_pud = setup_gpio(args.pir, args.trig, args.echo, args.led, args.pud)
    log(f"[START] {dt.datetime.now():%F %T} server={args.server} device={args.device}")
    log(f"pins(BCM) PIR:{args.pir} TRIG:{args.trig} ECHO:{args.echo} LED:{args.led}  PUD={used_pud}")
    report(args.server, args.device, "센서 클라이언트 기동 (0.2s 추적모드, REARM_MODE=%s)" % REARM_MODE)

    if args.pud == "auto":
        used_pud = maybe_switch_pud_auto(args.pir, used_pud)
        if used_pud == "PUD_UP":
            log("PIR 입력 모드 자동 전환: PUD_UP")

    # PIR 워밍업
    if args.warmup > 0:
        log(f"PIR 워밍업 중... {args.warmup}s 대기")
        end = time.monotonic() + args.warmup
        next_tick = 0
        while time.monotonic() < end:
            if time.monotonic() >= next_tick:
                remain = int(end - time.monotonic())
                log(f"  ...{remain}s 남음 (PIR={GPIO.input(args.pir)})")
                next_tick = time.monotonic() + 1
            time.sleep(0.05)
        log("✅ PIR 센서 준비 완료")

    # 상태 변수
    system_active   = True
    in_cooldown     = False
    cooldown_until  = 0
    prev_pir        = GPIO.input(args.pir)
    armed           = True          # 다음 감지를 받을 준비 상태
    tracking_active = False         # PIR=HIGH 동안 0.2s 간격 추적 활성화 여부
    last_power_poll = 0
    last_report     = 0
    last_hb         = 0

    # TDM 파라미터
    slot_ms   = max(20, int(args.slot_ms))
    num_slots = max(4,  int(args.num_slots))
    frame_ms  = slot_ms * num_slots
    start_ms  = now_ms()
    next_slot_start = start_ms
    slot_index = 0

    def do_power_poll():
        nonlocal system_active, last_power_poll
        if ms_since(last_power_poll) >= POWER_POLL_MS:
            last_power_poll = now_ms()
            new_flag = get_power_flag(args.server, args.device, default=True)
            if new_flag != system_active:
                system_active = new_flag
                log(f"POWER FLAG -> {system_active}")

    def do_pir_sample():
        nonlocal prev_pir, armed, tracking_active, last_report
        cur = GPIO.input(args.pir)

        # 에지 로깅
        if cur != prev_pir:
            log("PIR:", "사람 감지됨" if cur else "움직임 없음")
            prev_pir = cur
            if cur == 0:
                # PIR이 LOW로 내려가면 언제든 재무장
                armed = True
                tracking_active = False
                if ms_since(last_report) >= REPORT_MIN_INTERVAL_MS:
                    report(args.server, args.device, "PIR 미감지")
                    last_report = now_ms()

        # 시스템 ON, 비-쿨다운, PIR=HIGH, armed → 추적 시작
        if system_active and not in_cooldown and cur == 1 and armed:
            tracking_active = True

        # 쿨다운 중엔 항상 추적 비활성
        if in_cooldown:
            tracking_active = False

    def do_ultra_track():
        # 0.2s(프레임)마다 1회 측정
        nonlocal in_cooldown, cooldown_until, tracking_active, armed, last_report
        if not tracking_active:
            return
        d, err = measure_once_cm(args.trig, args.echo)
        if d is None:
            # 에러 로깅 (타임아웃 등). 스팸 방지 위해 서버 보고는 스로틀
            if err == "ECHO_LOW_TIMEOUT":
                log("No echo (LOW TIMEOUT): 배선/전원 확인")
            elif err == "ECHO_HIGH_TIMEOUT":
                log("No echo (HIGH TIMEOUT): 레벨시프터/분압 확인")
            else:
                log("No echo received.")
            if ms_since(last_report) >= REPORT_MIN_INTERVAL_MS:
                report(args.server, args.device, "초음파 응답 없음")
                last_report = now_ms()
            return

        log(f"[TRACK] distance={d} cm (every ~{frame_ms/1000:.1f}s)")
        if d <= DISTANCE_THRESHOLD_CM:
            # 조건 도달 → 즉시 LED 점등 + 추적 중단 + 쿨다운
            led_set(args.led, True)
            in_cooldown     = True
            cooldown_until  = now_ms() + COOLDOWN_MS
            tracking_active = False
            armed           = False   # edge 정책에서 재무장 방지(에지 기다림)
            log("LED ON - 근접 감지됨 (추적 중단)")
            if ms_since(last_report) >= REPORT_MIN_INTERVAL_MS:
                report(args.server, args.device, "사람 감지 및 LED 점등", distance=d)
                last_report = now_ms()

    def do_led_hb():
        nonlocal in_cooldown, last_hb, armed, tracking_active
        # 쿨다운 종료 처리
        if in_cooldown and now_ms() >= cooldown_until:
            in_cooldown = False
            led_set(args.led, False)
            log("쿨다운 종료 → LED OFF")

            # ★ 재무장 정책 적용
            if REARM_MODE == "cooldown":
                if GPIO.input(args.pir) == 1:
                    armed = True
                    tracking_active = True   # PIR HIGH 유지 시 즉시 추적 재개
                else:
                    armed = True
                    tracking_active = False
            else:
                # edge 모드: PIR LOW→HIGH 에지 올 때까지 대기
                tracking_active = False

        # 하트비트 (1초)
        if ms_since(last_hb) >= 1000:
            last_hb = now_ms()
            cur = GPIO.input(args.pir)
            led_state = GPIO.input(args.led)
            log(f"[HB] PIR={'HIGH' if cur else 'LOW '}  LED={'ON' if led_state else 'OFF'}  TRACK={'ON' if tracking_active else 'OFF'}  ARMED={'Y' if armed else 'N'}")

    try:
        log(f"[TDM] FRAME={frame_ms}ms  SLOT={slot_ms}ms × {num_slots} slots (ULTRA @ ~{frame_ms/1000:.1f}s)")
        log(f"[MODE] REARM_MODE={REARM_MODE}")
        while True:
            now = now_ms()
            if now < next_slot_start:
                time.sleep(min(0.02, (next_slot_start - now)/1000.0))
                continue

            task = SLOT_TASKS.get(slot_index)
            if task == "POWER_POLL":  do_power_poll()
            elif task == "PIR_SAMPLE": do_pir_sample()
            elif task == "ULTRA_TRACK": do_ultra_track()
            elif task == "LED_HB":     do_led_hb()

            slot_index = (slot_index + 1) % num_slots
            next_slot_start += slot_ms

            # 큰 지연시 재정렬
            if now_ms() - next_slot_start > slot_ms * 2:
                base = now_ms()
                slot_index = ((base - start_ms) // slot_ms) % num_slots
                next_slot_start = base + slot_ms

    except KeyboardInterrupt:
        pass
    finally:
        led_set(args.led, False)
        GPIO.cleanup()
        report(args.server, args.device, "센서 클라이언트 종료 (0.2s 추적모드, REARM_MODE=%s)" % REARM_MODE)
        log(f"[STOP] {dt.datetime.now():%F %T}")

if __name__ == "__main__":
    main()
