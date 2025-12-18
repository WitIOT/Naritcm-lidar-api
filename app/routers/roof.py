import os, time, threading
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from periphery import GPIO

router = APIRouter(prefix="/roof", tags=["roof"])

# ===== ENV (Roof Control) =====
GPIO_CHIP = os.getenv("GPIO_CHIP", "/dev/gpiochip0")

# Output lines (periphery uses GPIO "lines" not BCM pins)
LINE_OPEN  = int(os.getenv("LINE_OPEN",  "24"))   # e.g. DO2
LINE_CLOSE = int(os.getenv("LINE_CLOSE", "23"))   # e.g. DO1
DEFAULT_PULSE_MS = int(os.getenv("DEFAULT_PULSE_MS", "800"))

# Limit switch (DI1)
LINE_DI1 = int(os.getenv("LINE_DI1", "5"))
DI1_ACTIVE_HIGH = os.getenv("DI1_ACTIVE_HIGH", "true").lower() in ("1","true","yes","y","on")
DI1_DEBOUNCE_MS = int(os.getenv("DI1_DEBOUNCE_MS", "50"))

class DoorController:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"  # idle|opening|closing|hold_open|hold_close
        self.gpio_open = GPIO(GPIO_CHIP, LINE_OPEN, "out")
        self.gpio_close = GPIO(GPIO_CHIP, LINE_CLOSE, "out")
        self.all_low()

    def all_low(self):
        self.gpio_open.write(False)
        self.gpio_close.write(False)

    def pulse(self, target: Literal["open","close"], ms: int):
        if ms <= 0 or ms > 30_000:
            raise ValueError("ms must be 1..30000")
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
                self.gpio_open.write(True)
                self.state = "hold_open"
            else:
                self.gpio_close.write(True)
                self.state = "hold_close"

    def stop(self):
        with self.lock:
            self.all_low()
            self.state = "idle"

    def status(self):
        with self.lock:
            return {
                "state": self.state,
                "open_line": LINE_OPEN,
                "close_line": LINE_CLOSE,
                "open_value": int(self.gpio_open.read()),
                "close_value": int(self.gpio_close.read()),
            }

    def close(self):
        try:
            self.stop()
        finally:
            try: self.gpio_open.close()
            except Exception: pass
            try: self.gpio_close.close()
            except Exception: pass

class DebouncedInput:
    def __init__(self):
        self.gpio = GPIO(GPIO_CHIP, LINE_DI1, "in")
        self._lock = threading.Lock()
        self._raw = False
        self._stable = False
        self._last_change = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _read_norm(self) -> bool:
        v = bool(self.gpio.read())
        return v if DI1_ACTIVE_HIGH else (not v)

    def _loop(self):
        self._raw = self._read_norm()
        self._stable = self._raw
        self._last_change = time.monotonic()

        while not self._stop.is_set():
            v = self._read_norm()
            now = time.monotonic()
            with self._lock:
                if v != self._raw:
                    self._raw = v
                    self._last_change = now
                # become stable after debounce window
                if (now - self._last_change) * 1000 >= DI1_DEBOUNCE_MS:
                    self._stable = self._raw
            time.sleep(0.01)  # 10ms

    def read(self) -> bool:
        with self._lock:
            return bool(self._stable)

    def close(self):
        self._stop.set()
        try:
            self._thread.join(timeout=0.5)
        except Exception:
            pass
        try:
            self.gpio.close()
        except Exception:
            pass

door: DoorController | None = None
di1: DebouncedInput | None = None

def init_roof():
    global door, di1
    if door is None:
        door = DoorController()
    if di1 is None:
        di1 = DebouncedInput()

def close_roof():
    global door, di1
    if di1 is not None:
        di1.close()
        di1 = None
    if door is not None:
        door.close()
        door = None

@router.post("/open")
def roof_open(ms: int = Query(DEFAULT_PULSE_MS, ge=1, le=30000)):
    if door is None:
        raise HTTPException(status_code=500, detail="Roof GPIO not initialized")
    threading.Thread(target=door.pulse, args=("open", ms), daemon=True).start()
    return {"ok": True, "action": "open", "ms": ms}

@router.post("/close")
def roof_close(ms: int = Query(DEFAULT_PULSE_MS, ge=1, le=30000)):
    if door is None:
        raise HTTPException(status_code=500, detail="Roof GPIO not initialized")
    threading.Thread(target=door.pulse, args=("close", ms), daemon=True).start()
    return {"ok": True, "action": "close", "ms": ms}

@router.post("/hold")
def roof_hold(target: Literal["open","close"] = Query(...)):
    if door is None:
        raise HTTPException(status_code=500, detail="Roof GPIO not initialized")
    door.hold(target)
    return {"ok": True, "action": "hold", "target": target}

@router.post("/stop")
def roof_stop():
    if door is None:
        raise HTTPException(status_code=500, detail="Roof GPIO not initialized")
    door.stop()
    return {"ok": True, "action": "stop"}

@router.get("/status")
def roof_status():
    if door is None:
        raise HTTPException(status_code=500, detail="Roof GPIO not initialized")
    return {"ok": True, "roof": door.status()}

@router.get("/limit/status")
def limit_status():
    if di1 is None:
        raise HTTPException(status_code=500, detail="Limit input not initialized")
    state = di1.read()
    return {
        "ok": True,
        "limit": {
            "input": "DI1",
            "gpio_line": LINE_DI1,
            "active_high": DI1_ACTIVE_HIGH,
            "debounce_ms": DI1_DEBOUNCE_MS,
            "state": "ON" if state else "OFF",
            "value": int(state),
        }
    }
