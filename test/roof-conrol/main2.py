import os
import time
import threading
from typing import Literal, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from periphery import GPIO

app = FastAPI(title="IRIV Door & Limit Switch API", version="1.2")

# ===== Config =====
GPIO_CHIP = os.getenv("GPIO_CHIP", "/dev/gpiochip0")

LINE_OPEN  = int(os.getenv("LINE_OPEN",  "24"))  # DO1/DO2 mapping
LINE_CLOSE = int(os.getenv("LINE_CLOSE", "23"))
DEFAULT_PULSE_MS = int(os.getenv("DEFAULT_PULSE_MS", "800"))

LINE_DI1 = int(os.getenv("LINE_DI1", "17"))  # Limit Switch DI1
DI1_ACTIVE_HIGH = os.getenv("DI1_ACTIVE_HIGH", "true").lower() == "true"
DI1_DEBOUNCE_MS = int(os.getenv("DI1_DEBOUNCE_MS", "50"))

# ===== DO Manager =====
class DOManager:
    def __init__(self, chip_path: str, line_open: int, line_close: int):
        self.lock = threading.Lock()
        self.state: Literal["idle", "opening", "closing", "holding_open", "holding_close"] = "idle"
        self.chip_path = chip_path
        self.gpio_open = GPIO(chip_path, line_open,  "out")
        self.gpio_close= GPIO(chip_path, line_close, "out")
        self.all_low()

    def all_low(self):
        self.gpio_open.write(False)
        self.gpio_close.write(False)

    def pulse(self, target: Literal["open","close"], ms: int):
        if not (1 <= ms <= 5000):
            raise ValueError("pulse ms must be 1..5000")
        with self.lock:
            self.all_low()
            if target == "open":
                self.state = "opening"
                self.gpio_open.write(True)
            else:
                self.state = "closing"
                self.gpio_close.write(True)
        try:
            time.sleep(ms / 1000.0)
        finally:
            with self.lock:
                self.all_low()
                self.state = "idle"

    def hold(self, target: Literal["open","close"]):
        with self.lock:
            self.all_low()
            if target == "open":
                self.gpio_open.write(True);  self.state = "holding_open"
            else:
                self.gpio_close.write(True); self.state = "holding_close"

    def stop(self):
        with self.lock:
            self.all_low(); self.state = "idle"

    def status(self):
        return {"state": self.state}

# ===== DI Reader (DI1) =====
class DIReader:
    def __init__(self, chip_path: str, line_no: int, active_high: bool, debounce_ms: int):
        self.gpio = GPIO(chip_path, line_no, "in")
        self.active_high = active_high
        self.debounce_s = max(debounce_ms, 0) / 1000.0

    def read_raw(self) -> int:
                return 1 if self.gpio.read() else 0

    def read(self) -> bool:
        votes = 0
        reads = 3
        for _ in range(reads):
            votes += self.read_raw()
            if self.debounce_s: time.sleep(self.debounce_s / reads)
        raw_level = 1 if votes >= 2 else 0
        return bool(raw_level if self.active_high else (1 - raw_level))

manager = DOManager(GPIO_CHIP, LINE_OPEN, LINE_CLOSE)
di1 = DIReader(GPIO_CHIP, LINE_DI1, DI1_ACTIVE_HIGH, DI1_DEBOUNCE_MS)

# ===== Models =====
class PulseRequest(BaseModel):
    ms: Optional[int] = None

# ===== Routes =====
@app.get("/health")
def health():
    return {"ok": True, "service": "iriv-door-api", "version": "1.2"}

@app.get("/door/status")
def door_status():
    return {"ok": True, "status": manager.status()}

@app.post("/door/open")
def door_open(body: PulseRequest | None = None, ms: int = Query(None, ge=1, le=5000)):
    pulse_ms = ms or (body.ms if body else None) or DEFAULT_PULSE_MS
    try:
        manager.pulse("open", pulse_ms)
        return {"ok": True, "action": "open", "pulse_ms": pulse_ms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/door/close")
def door_close(body: PulseRequest | None = None, ms: int = Query(None, ge=1, le=5000)):
    pulse_ms = ms or (body.ms if body else None) or DEFAULT_PULSE_MS
    try:
        manager.pulse("close", pulse_ms)
        return {"ok": True, "action": "close", "pulse_ms": pulse_ms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/door/hold")
def door_hold(target: Literal["open","close"]):
    try:
        manager.hold(target)
        return {"ok": True, "action": f"hold_{target}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/door/stop")
def door_stop():
    manager.stop()
    return {"ok": True, "action": "stop"}

# ---- NEW: Read DI1 (Limit Switch) ----
@app.get("/limit/status")
def limit_status():
    state = di1.read()
    return {
        "ok": True,
        "limit": {
            "input": "DI1",
            "gpio_line": LINE_DI1,
            "active_high": DI1_ACTIVE_HIGH,
            "debounce_ms": DI1_DEBOUNCE_MS,
            "state": "ON" if state else "OFF",
            "value": int(state)
        }
    }