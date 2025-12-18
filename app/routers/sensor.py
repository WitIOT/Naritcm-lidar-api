import os, time, asyncio, math
from typing import Any, Dict, List, Tuple
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pymodbus.client import ModbusSerialClient
from influxdb_client import InfluxDBClient, Point, WriteOptions

router = APIRouter()

# ===== ENV (Modbus/RS485) =====
SERIAL_PORT   = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE      = int(os.getenv("BAUDRATE", "9600"))
PARITY        = os.getenv("PARITY", "N")        # N/E/O
BYTESIZE      = int(os.getenv("BYTESIZE", "8")) # 7/8
STOPBITS      = int(os.getenv("STOPBITS", "1")) # 1/2
TIMEOUT_S     = float(os.getenv("TIMEOUT_S", "1.0"))

READ_TABLE    = os.getenv("READ_TABLE", "holding").lower()  # holding|input
REG_START     = int(os.getenv("REG_START", "0"))            # 0-based (pymodbus)
REG_COUNT     = int(os.getenv("REG_COUNT", "2"))

TEMP_INDEX    = int(os.getenv("TEMP_INDEX", "0"))
HUMI_INDEX    = int(os.getenv("HUMI_INDEX", "1"))
SCALE_DIV     = float(os.getenv("SCALE_DIV", "10"))

INDOOR_ID     = int(os.getenv("INDOOR_ID", "1"))
OUTDOOR_ID    = int(os.getenv("OUTDOOR_ID", "2"))

POLL_MS       = int(os.getenv("POLL_MS", "1000"))

client = ModbusSerialClient(


# ===== InfluxDB (optional) =====
INFLUX_WRITE_ENABLE = os.getenv("INFLUX_WRITE_ENABLE", "false").lower() in ("1","true","yes","y","on")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "")
INFLUX_MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "env")
INFLUX_TAG_SITE = os.getenv("INFLUX_TAG_SITE", "site")

_influx_client = None
_influx_write_api = None

def influx_init():
    global _influx_client, _influx_write_api
    if not INFLUX_WRITE_ENABLE:
        return
    if not (INFLUX_URL and INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET):
        return
    _influx_client = InfluxDBClient(
        url=INFLUX_URL,
        token=INFLUX_TOKEN,
        org=INFLUX_ORG,
        timeout=10_000,
    )
    _influx_write_api = _influx_client.write_api(
        write_options=WriteOptions(
            batch_size=200,
            flush_interval=2000,
            jitter_interval=500,
            retry_interval=5000,
        )
    )

def influx_close():
    global _influx_client, _influx_write_api
    try:
        if _influx_write_api:
            _influx_write_api.flush()
    except Exception:
        pass
    try:
        if _influx_client:
            _influx_client.close()
    except Exception:
        pass
    _influx_client = None
    _influx_write_api = None

def influx_write_env(ts_ms: int, label: str, temp_c: float, rh: float, dew_c: float):
    if not INFLUX_WRITE_ENABLE or _influx_write_api is None:
        return

    p_humi = (
        Point(INFLUX_MEASUREMENT)
        .tag("location", label)
        .tag("table", READ_TABLE)
        .field("humi", float(rh))
        .time(ts_ms * 1_000_000)  # ms -> ns
    )
    p_temp = (
        Point(INFLUX_MEASUREMENT)
        .tag("location", label)
        .tag("table", READ_TABLE)
        .field("temp", float(temp_c))
        .time(ts_ms * 1_000_000)
    )
    p_dew = (
        Point(INFLUX_MEASUREMENT)
        .tag("location", label)
        .tag("table", READ_TABLE)
        .field("dewpoint", float(dew_c))
        .time(ts_ms * 1_000_000)
    )

    _influx_write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=[p_humi, p_temp, p_dew])
    method="rtu",
    port=SERIAL_PORT,
    baudrate=BAUDRATE,
    bytesize=BYTESIZE,
    parity=PARITY,
    stopbits=STOPBITS,
    timeout=TIMEOUT_S,
)

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
    return humi, 

# ===== Derived metric =====
def dew_point_c(temp_c: float, rh_percent: float) -> float:
    """
    Dew point (°C) using Magnus formula.
    Valid for typical ambient temperatures; returns NaN if RH is invalid.
    """
    if rh_percent <= 0 or rh_percent > 100:
        return float("nan")
    a, b = 17.62, 243.12  # constants for water over liquid surface
    gamma = (a * temp_c) / (b + temp_c) + math.log(rh_percent / 100.0)
    return (b * gamma) / (a - gamma)
temp

@router.get("/api/sensor/{unit_id}")
def read_sensor_unit(unit_id: int) -> Dict[str, Any]:
    try:
        regs = read_raw_regs(unit=unit_id)
        humi, temp = to_humi_temp(regs)
        name = "indoor" if unit_id == INDOOR_ID else ("outdoor" if unit_id == OUTDOOR_ID else f"unit_{unit_id}")
        return {
            "ok": True, "name": name, "unit_id": unit_id, "table": READ_TABLE,
            "start": REG_START, "count": REG_COUNT,
            "raw_registers": regs,
            "humi": round(humi, 1), "temp": round(temp, 1), "dewpoint": round(dew_point_c(temp, humi), 1),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e), "unit_id": unit_id})

@router.get("/api/sensor")
def read_sensor_both() -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "table": READ_TABLE, "start": REG_START, "count": REG_COUNT, "indoor": None, "outdoor": None}
    try:
        regs1 = read_raw_regs(unit=INDOOR_ID); h1, t1 = to_humi_temp(regs1)
        out["indoor"] = {"raw": regs1, "humi": round(h1,1), "temp": round(t1,1), "dewpoint": round(dew_point_c(t1, h1),1), "unit_id": INDOOR_ID}
    except Exception as e:
        out["indoor"] = {"error": str(e), "unit_id": INDOOR_ID}; out["ok"] = False
    try:
        regs2 = read_raw_regs(unit=OUTDOOR_ID); h2, t2 = to_humi_temp(regs2)
        out["outdoor"] = {"raw": regs2, "humi": round(h2,1), "temp": round(t2,1), "dewpoint": round(dew_point_c(t2, h2),1), "unit_id": OUTDOOR_ID}
    except Exception as e:
        out["outdoor"] = {"error": str(e), "unit_id": OUTDOOR_ID}; out["ok"] = False
    return out

# ===== WebSocket realtime =====
_ws_clients: set[WebSocket] = set()
_poll_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None

async def _poll_loop():
    assert _stop_event is not None
    while not _stop_event.is_set():
        payload: Dict[str, Any] = {"ts": int(time.time()*1000), "ok": True}

        def pack(unit_id: int, label: str):
            try:
                regs = read_raw_regs(unit=unit_id)
                h, t = to_humi_temp(regs)
                return {label: {"unit_id": unit_id, "raw": list(regs), "humi": round(h,1), "temp": round(t,1), "dewpoint": round(dew_point_c(t, h),1)}}
            except Exception as e:
                return {label: {"unit_id": unit_id, "error": str(e)}}

        payload.update(pack(INDOOR_ID, "indoor"))
        payload.update(pack(OUTDOOR_ID, "outdoor"))
        if "error" in payload["indoor"] or "error" in payload["outdoor"]:
            payload["ok"] = False

        stale = []
        for ws in list(_ws_clients):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            _ws_clients.discard(ws)

        await asyncio.sleep(max(POLL_MS, 100) / 1000)

@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            # Keep connection alive; client may send pings/text
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(ws)

async def start_sensor_tasks():
    global _poll_task, _stop_event
    if _poll_task and not _poll_task.done():
        return
    influx_init()
    _stop_event = asyncio.Event()
    _poll_task = asyncio.create_task(_poll_loop())

async def stop_sensor_tasks():
    global _poll_task, _stop_event
    try:
        if _stop_event is not None:
            _stop_event.set()
        if _poll_task is not None:
            await asyncio.sleep(0)  # yield
            _poll_task.cancel()
            try:
                await _poll_task
            except Exception:
                pass
    finally:
        _poll_task = None
        _stop_event = None
        try:
            client.close()
        except Exception:
            pass
        influx_close()
        try:
        except Exception:
            pass
