import os
import time
import threading
from typing import Literal, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from periphery import GPIO
from pymodbus.client import ModbusSerialClient

app = FastAPI(title="IRIV PiControl API", version="2.1")

# =========================
# RS485 / Modbus (Temp&Humi)
# =========================
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE = int(os.getenv("BAUDRATE", "9600"))
PARITY = os.getenv("PARITY", "N")
BYTESIZE = int(os.getenv("BYTESIZE", "8"))
STOPBITS = int(os.getenv("STOPBITS", "1"))
TIMEOUT_S = float(os.getenv("TIMEOUT_S", "1.0"))

REG_START = int(os.getenv("REG_START", "0"))
REG_COUNT = int(os.getenv("REG_COUNT", "2"))
TEMP_INDEX = int(os.getenv("TEMP_INDEX", "0"))
HUMI_INDEX = int(os.getenv("HUMI_INDEX", "1"))
SCALE_DIV = float(os.getenv("SCALE_DIV", "10"))

INDOOR_ID = int(os.getenv("INDOOR_ID", "1"))
OUTDOOR_ID = int(os.getenv("OUTDOOR_ID", "2"))

modbus = ModbusSerialClient(
    port=SERIAL_PORT,
    baudrate=BAUDRATE,
    parity=PARITY,
    bytesize=BYTESIZE,
    stopbits=STOPBITS,
    timeout=TIMEOUT_S,
)

def read_sensor(unit_id: int) -> Tuple[float, float, Tuple[int, ...]]:
    if not modbus.connected and not modbus.connect():
        raise RuntimeError("Modbus not connected")

    rr = modbus.read_holding_registers(REG_START, REG_COUNT, slave=unit_id)
    if rr.isError():
        raise RuntimeError(str(rr))

    regs = tuple(rr.registers)
    need = max(HUMI_INDEX, TEMP_INDEX) + 1
    if len(regs) < need:
        raise RuntimeError(f"Need at least {need} registers, got {len(regs)}")

    humi = regs[HUMI_INDEX] / SCALE_DIV
    temp = regs[TEMP_INDEX] / SCALE_DIV
    return humi, temp, regs

# =========================
# GPIO (Door + Limit Switch)
# =========================
GPIO_CHIP = os.getenv("GPIO_CHIP", "/dev/gpiochip0")

LINE_OPEN = int(os.getenv("LINE_OPEN", "24")) # DI1
LINE_CLOSE = int(os.getenv("LINE_CLOSE", "25")) # DI2
DEFAULT_PULSE_MS = int(os.getenv("DEFAULT_PULSE_MS", "800"))

LINE_DI1 = int(os.getenv("LINE_DI1", "17"))
DI1_ACTIVE_HIGH = os.getenv("DI1_ACTIVE_HIGH", "true").lower() == "true"
DI1_DEBOUNCE_MS = int(os.getenv("DI1_DEBOUNCE_MS", "50"))

class DOManager:
    def __init__(self, chip_path: str, line_open: int, line_close: int):
        self.lock = threading.Lock()
        self.state: Literal["idle", "opening", "closing", "holding_open", "holding_close"] = "idle"
        self.do_open = GPIO(chip_path, line_open, "out")
        self.do_close = GPIO(chip_path, line_close, "out")
        self.all_low()

    def all_low(self):
        self.do_open.write(False)
        self.do_close.write(False)

    def pulse(self, target: Literal["open", "close"], ms: int):
        if not (1 <= ms <= 5000):
            raise ValueError("pulse ms must be 1..5000")
        with self.lock:
            self.all_low()
            if target == "open":
                self.state = "opening"
                self.do_open.write(True)
            else:
                self.state = "closing"
                self.do_close.write(True)
        try:
            time.sleep(ms / 1000.0)
        finally:
            with self.lock:
                self.all_low()
                self.state = "idle"

    def hold(self, target: Literal["open", "close"]):
        with self.lock:
            self.all_low()
            if target == "open":
                self.do_open.write(True)
                self.state = "holding_open"
            else:
                self.do_close.write(True)
                self.state = "holding_close"

    def stop(self):
        with self.lock:
            self.all_low()
            self.state = "idle"

    def status(self):
        return {"state": self.state}

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
            if self.debounce_s:
                time.sleep(self.debounce_s / reads)
        raw_level = 1 if votes >= 2 else 0
        return bool(raw_level if self.active_high else (1 - raw_level))

manager = DOManager(GPIO_CHIP, LINE_OPEN, LINE_CLOSE)
di1 = DIReader(GPIO_CHIP, LINE_DI1, DI1_ACTIVE_HIGH, DI1_DEBOUNCE_MS)

class PulseRequest(BaseModel):
    ms: Optional[int] = None

# =========================
# Routes (Door / Limit)
# =========================
@app.get("/health")
def health():
    return {"ok": True, "service": "iriv-picontrol-api", "version": app.version}

@app.get("/door/status")
def door_status():
    return {"ok": True, "status": manager.status()}

@app.post("/door/open")
def door_open(body: Optional[PulseRequest] = None, ms: int = Query(None, ge=1, le=5000)):
    pulse_ms = ms or (body.ms if body else None) or DEFAULT_PULSE_MS
    try:
        manager.pulse("open", pulse_ms)
        return {"ok": True, "action": "open", "pulse_ms": pulse_ms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/door/close")
def door_close(body: Optional[PulseRequest] = None, ms: int = Query(None, ge=1, le=5000)):
    pulse_ms = ms or (body.ms if body else None) or DEFAULT_PULSE_MS
    try:
        manager.pulse("close", pulse_ms)
        return {"ok": True, "action": "close", "pulse_ms": pulse_ms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/door/hold")
def door_hold(target: Literal["open", "close"]):
    try:
        manager.hold(target)
        return {"ok": True, "action": f"hold_{target}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/door/stop")
def door_stop():
    manager.stop()
    return {"ok": True, "action": "stop"}

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
            "value": int(state),
        },
    }

# =========================
# Routes (Sensor)
# =========================
@app.get("/api/sensor")
def read_both():
    out = {"ok": True, "indoor": None, "outdoor": None}
    try:
        h1, t1, r1 = read_sensor(INDOOR_ID)
        out["indoor"] = {"unit_id": INDOOR_ID, "raw": r1, "humi": round(h1, 1), "temp": round(t1, 1)}
    except Exception as e:
        out["indoor"] = {"unit_id": INDOOR_ID, "error": str(e)}
        out["ok"] = False\
        

    try:
        h2, t2, r2 = read_sensor(OUTDOOR_ID)
        out["outdoor"] = {"unit_id": OUTDOOR_ID, "raw": r2, "humi": round(h2, 1), "temp": round(t2, 1)}
    except Exception as e:
        out["outdoor"] = {"unit_id": OUTDOOR_ID, "error": str(e)}
        out["ok"] = False

    if out["ok"]:
        return out
    return JSONResponse(status_code=500, content=out)
