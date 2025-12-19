import os, time, asyncio, threading
from typing import Literal, Optional, Dict, Any, Tuple
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ===== GPIO =====
from periphery import GPIO

# ===== Modbus =====
from pymodbus.client import ModbusSerialClient

app = FastAPI(title="IRIV PiControl API", version="2.0")

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE    = int(os.getenv("BAUDRATE", "9600"))
PARITY      = os.getenv("PARITY", "N")
BYTESIZE    = int(os.getenv("BYTESIZE", "8"))
STOPBITS    = int(os.getenv("STOPBITS", "1"))
TIMEOUT_S   = float(os.getenv("TIMEOUT_S", "1.0"))

REG_START   = int(os.getenv("REG_START", "0"))
REG_COUNT   = int(os.getenv("REG_COUNT", "2"))
TEMP_INDEX  = int(os.getenv("TEMP_INDEX", "0"))
HUMI_INDEX  = int(os.getenv("HUMI_INDEX", "1"))
SCALE_DIV   = float(os.getenv("SCALE_DIV", "10"))

INDOOR_ID  = int(os.getenv("INDOOR_ID", "1"))
OUTDOOR_ID = int(os.getenv("OUTDOOR_ID", "2"))

modbus = ModbusSerialClient(
    # method="rtu",
    port=SERIAL_PORT,
    baudrate=BAUDRATE,
    parity=PARITY,
    bytesize=BYTESIZE,
    stopbits=STOPBITS,
    timeout=TIMEOUT_S,
)

def read_sensor(unit_id: int) -> Tuple[float, float, Tuple[int,...]]:
    if not modbus.connected and not modbus.connect():
        raise RuntimeError("Modbus not connected")

    rr = modbus.read_holding_registers(REG_START, REG_COUNT, slave=unit_id)
    if rr.isError():
        raise RuntimeError(str(rr))

    regs = tuple(rr.registers)
    humi = regs[HUMI_INDEX] / SCALE_DIV
    temp = regs[TEMP_INDEX] / SCALE_DIV
    return humi, temp, regs

GPIO_CHIP = os.getenv("GPIO_CHIP", "/dev/gpiochip0")

LINE_OPEN  = int(os.getenv("LINE_OPEN",  "24"))
LINE_CLOSE = int(os.getenv("LINE_CLOSE", "23"))
LINE_DI1   = int(os.getenv("LINE_DI1", "17"))

class DOManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"
        self.do_open  = GPIO(GPIO_CHIP, LINE_OPEN,  "out")
        self.do_close = GPIO(GPIO_CHIP, LINE_CLOSE, "out")
        self.all_low()

    def all_low(self):
        self.do_open.write(False)
        self.do_close.write(False)

    def pulse(self, target: Literal["open","close"], ms: int):
        with self.lock:
            self.all_low()
            if target == "open":
                self.do_open.write(True)
            else:
                self.do_close.write(True)
        time.sleep(ms/1000)
        self.all_low()

manager = DOManager()
limit_di1 = GPIO(GPIO_CHIP, LINE_DI1, "in")

class PulseRequest(BaseModel):
    ms: Optional[int] = 800

@app.post("/door/open")
def door_open(body: PulseRequest):
    manager.pulse("open", body.ms)
    return {"ok": True}

@app.post("/door/close")
def door_close(body: PulseRequest):
    manager.pulse("close", body.ms)
    return {"ok": True}

@app.get("/limit/status")
def limit_status():
    state = limit_di1.read()
    return {"ok": True, "DI1": int(state)}

@app.get("/api/sensor")
def read_both():
    try:
        h1, t1, r1 = read_sensor(INDOOR_ID)
        h2, t2, r2 = read_sensor(OUTDOOR_ID)
        return {
            "ok": True,
            "indoor": {"humi": h1, "temp": t1, "raw": r1},
            "outdoor": {"humi": h2, "temp": t2, "raw": r2},
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
