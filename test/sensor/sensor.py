import os, time, asyncio
from typing import List, Tuple, Dict, Any
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pymodbus.client import ModbusSerialClient  # RTU/Serial

# ===== ENV =====
SERIAL_PORT   = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE      = int(os.getenv("BAUDRATE", "9600"))
PARITY        = os.getenv("PARITY", "N")        # N/E/O
BYTESIZE      = int(os.getenv("BYTESIZE", "8")) # 7/8
STOPBITS      = int(os.getenv("STOPBITS", "1")) # 1/2
TIMEOUT_S     = float(os.getenv("TIMEOUT_S", "1.0"))

READ_TABLE    = os.getenv("READ_TABLE", "holding").lower()  # holding|input
REG_START     = int(os.getenv("REG_START", "0"))   # 0-based (pymodbus)
REG_COUNT     = int(os.getenv("REG_COUNT", "2"))

TEMP_INDEX    = int(os.getenv("TEMP_INDEX", "0"))
HUMI_INDEX    = int(os.getenv("HUMI_INDEX", "1"))
SCALE_DIV     = float(os.getenv("SCALE_DIV", "10"))

INDOOR_ID     = int(os.getenv("INDOOR_ID", "1"))
OUTDOOR_ID    = int(os.getenv("OUTDOOR_ID", "2"))

POLL_MS       = int(os.getenv("POLL_MS", "1000"))

# ===== Modbus client (RTU over /dev/ttyACM0) =====
client = ModbusSerialClient(
    method="rtu",
    port=SERIAL_PORT,
    baudrate=BAUDRATE,
    bytesize=BYTESIZE,
    parity=PARITY,
    stopbits=STOPBITS,
    timeout=TIMEOUT_S,
)

app = FastAPI(title="RS485 Temp&Humi API")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ===== Helpers =====
def ensure_connected() -> None:
    if not client.connected:
        if not client.connect():
            raise RuntimeError("Modbus not connected")

def read_raw_regs(unit: int, address: int | None = None, count: int | None = None) -> Tuple[int, ...]:
    ensure_connected()
    addr = REG_START if address is None else address
    cnt  = REG_COUNT if count   is None else count

    if READ_TABLE == "input":
        rr = client.read_input_registers(addr, cnt, slave=unit)   # FC04
    else:
        rr = client.read_holding_registers(addr, cnt, slave=unit) # FC03
    if rr.isError():
        raise RuntimeError(f"Modbus error (unit {unit}): {rr}")
    return tuple(rr.registers)

def to_humi_temp(regs: List[int] | Tuple[int, ...]) -> Tuple[float, float]:
    need = max(HUMI_INDEX, TEMP_INDEX) + 1
    if len(regs) < need:
        raise ValueError(f"Need at least {need} registers, got {len(regs)}")
    humi = regs[HUMI_INDEX] / SCALE_DIV
    temp = regs[TEMP_INDEX] / SCALE_DIV
    return humi, temp

# ===== REST =====
@app.get("/api/sensor/{unit_id}")
def read_sensor_unit(unit_id: int) -> Dict[str, Any]:
    try:
        regs = read_raw_regs(unit=unit_id)
        humi, temp = to_humi_temp(regs)
        name = "indoor" if unit_id == INDOOR_ID else ("outdoor" if unit_id == OUTDOOR_ID else f"unit_{unit_id}")
        return {
            "ok": True, "name": name, "unit_id": unit_id, "table": READ_TABLE,
            "start": REG_START, "count": REG_COUNT,
            "raw_registers": regs,
            "humi": round(humi, 1), "temp": round(temp, 1),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "unit_id": unit_id})

@app.get("/api/sensor")
def read_sensor_both() -> Dict[str, Any]:
    out = {"ok": True, "table": READ_TABLE, "start": REG_START, "count": REG_COUNT, "indoor": None, "outdoor": None}
    try:
        regs1 = read_raw_regs(unit=INDOOR_ID); h1, t1 = to_humi_temp(regs1)
        out["indoor"] = {"raw": regs1, "humi": round(h1,1), "temp": round(t1,1), "unit_id": INDOOR_ID}
    except Exception as e:
        out["indoor"] = {"error": str(e), "unit_id": INDOOR_ID}; out["ok"] = False
    try:
        regs2 = read_raw_regs(unit=OUTDOOR_ID); h2, t2 = to_humi_temp(regs2)
        out["outdoor"] = {"raw": regs2, "humi": round(h2,1), "temp": round(t2,1), "unit_id": OUTDOOR_ID}
    except Exception as e:
        out["outdoor"] = {"error": str(e), "unit_id": OUTDOOR_ID}; out["ok"] = False
    return out

# ===== WebSocket realtime =====
clients: set[WebSocket] = set()

async def poll_loop():
    while True:
        payload = {"ts": int(time.time()*1000), "ok": True}

        def pack(unit_id: int, label: str):
            try:
                regs = read_raw_regs(unit=unit_id)
                h, t = to_humi_temp(regs)
                return {label: {"unit_id": unit_id, "raw": list(regs), "humi": round(h,1), "temp": round(t,1)}}
            except Exception as e:
                return {label: {"unit_id": unit_id, "error": str(e)}}

        payload.update(pack(INDOOR_ID, "indoor"))
        payload.update(pack(OUTDOOR_ID, "outdoor"))
        if "error" in payload["indoor"] or "error" in payload["outdoor"]:
            payload["ok"] = False

        stale = []
        for ws in clients:
            try: await ws.send_json(payload)
            except Exception: stale.append(ws)
        for ws in stale: clients.discard(ws)

        await asyncio.sleep(POLL_MS/1000)

@app.on_event("startup")
async def startup_event():
    await ws.accept(); clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)

@app.get("/")
def root():
    return FileResponse("app/static/index.html")
