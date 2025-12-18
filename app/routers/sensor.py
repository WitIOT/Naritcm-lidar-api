# app/routers/sensor.py
import os
import time
import math
import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pymodbus.client import ModbusSerialClient
from influxdb_client import InfluxDBClient, Point, WriteOptions

router = APIRouter(prefix="/api", tags=["sensor"])

# =========================================================
# RS485 / Modbus ENV
# =========================================================
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE = int(os.getenv("BAUDRATE", "9600"))
PARITY = os.getenv("PARITY", "N")
BYTESIZE = int(os.getenv("BYTESIZE", "8"))
STOPBITS = int(os.getenv("STOPBITS", "1"))
TIMEOUT_S = float(os.getenv("TIMEOUT_S", "1.0"))

READ_TABLE = os.getenv("READ_TABLE", "holding")  # holding | input
REG_START = int(os.getenv("REG_START", "0"))
REG_COUNT = int(os.getenv("REG_COUNT", "2"))

TEMP_INDEX = int(os.getenv("TEMP_INDEX", "0"))
HUMI_INDEX = int(os.getenv("HUMI_INDEX", "1"))
SCALE_DIV = float(os.getenv("SCALE_DIV", "10"))

INDOOR_ID = int(os.getenv("INDOOR_ID", "1"))
OUTDOOR_ID = int(os.getenv("OUTDOOR_ID", "2"))

POLL_MS = int(os.getenv("POLL_MS", "1000"))

# =========================================================
# Modbus Client (ต้องปิดวงเล็บตรงนี้ให้จบ!)
# =========================================================
client = ModbusSerialClient(
    method="rtu",
    port=SERIAL_PORT,
    baudrate=BAUDRATE,
    bytesize=BYTESIZE,
    parity=PARITY,
    stopbits=STOPBITS,
    timeout=TIMEOUT_S,
)

# =========================================================
# InfluxDB ENV (NO TAG_DEVICE)
# =========================================================
INFLUX_WRITE_ENABLE = (
    os.getenv("INFLUX_WRITE_ENABLE", "false").lower()
    in ("1", "true", "yes", "y", "on")
)

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "Narit")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "room2")
INFLUX_MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "room2")

_influx_client: Optional[InfluxDBClient] = None
_influx_write_api = None

# =========================================================
# InfluxDB helpers
# =========================================================
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

def influx_write_env(ts_ms: int, location: str, temp_c: float, rh: float, dew_c: float):
    if not INFLUX_WRITE_ENABLE or _influx_write_api is None:
        return

    p_humi = (
        Point(INFLUX_MEASUREMENT)
        .tag("location", location)      # indoor / outdoor
        .tag("table", READ_TABLE)        # holding / input
        .field("humi", float(rh))
        .time(ts_ms * 1_000_000)         # ms -> ns
    )

    p_temp = (
        Point(INFLUX_MEASUREMENT)
        .tag("location", location)
        .tag("table", READ_TABLE)
        .field("temp", float(temp_c))
        .time(ts_ms * 1_000_000)
    )

    p_dew = (
        Point(INFLUX_MEASUREMENT)
        .tag("location", location)
        .tag("table", READ_TABLE)
        .field("dewpoint", float(dew_c))
        .time(ts_ms * 1_000_000)
    )

    _influx_write_api.write(
        bucket=INFLUX_BUCKET,
        org=INFLUX_ORG,
        record=[p_humi, p_temp, p_dew],
    )

# =========================================================
# Data processing
# =========================================================
def read_raw_regs(unit: int):
    if not client.connect():
        raise RuntimeError("Modbus connect failed")

    if READ_TABLE == "holding":
        res = client.read_holding_registers(REG_START, REG_COUNT, unit=unit)
    else:
        res = client.read_input_registers(REG_START, REG_COUNT, unit=unit)

    if res.isError():
        raise RuntimeError(str(res))

    return res.registers

def to_humi_temp(regs):
    temp = regs[TEMP_INDEX] / SCALE_DIV
    humi = regs[HUMI_INDEX] / SCALE_DIV
    return humi, temp

def dew_point_c(temp_c: float, rh: float) -> float:
    if rh <= 0 or rh > 100:
        return float("nan")
    a, b = 17.62, 243.12
    gamma = (a * temp_c) / (b + temp_c) + math.log(rh / 100.0)
    return (b * gamma) / (a - gamma)

# =========================================================
# Polling + WebSocket
# =========================================================
_ws_clients: set[WebSocket] = set()
_poll_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None

async def _poll_loop():
    while not _stop_event.is_set():
        ts_ms = int(time.time() * 1000)
        payload = {"ts": ts_ms}

        for unit_id, label in (
            (INDOOR_ID, "indoor"),
            (OUTDOOR_ID, "outdoor"),
        ):
            try:
                regs = read_raw_regs(unit_id)
                h, t = to_humi_temp(regs)
                dp = dew_point_c(t, h)

                influx_write_env(ts_ms, label, t, h, dp)

                payload[label] = {
                    "unit_id": unit_id,
                    "raw": list(regs),
                    "humi": round(h, 1),
                    "temp": round(t, 1),
                    "dewpoint": round(dp, 1),
                }
            except Exception as e:
                payload[label] = {"unit_id": unit_id, "error": str(e)}

        dead = []
        for ws in _ws_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _ws_clients.discard(ws)

        await asyncio.sleep(POLL_MS / 1000.0)

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
        if _stop_event:
            _stop_event.set()
        if _poll_task:
            _poll_task.cancel()
            try:
                await _poll_task
            except Exception:
                pass
    finally:
        try:
            client.close()
        except Exception:
            pass
        influx_close()
        _poll_task = None
        _stop_event = None

# =========================================================
# API
# =========================================================
@router.get("/sensor")
def read_sensor_all():
    out = {}
    for unit_id, label in (
        (INDOOR_ID, "indoor"),
        (OUTDOOR_ID, "outdoor"),
    ):
        try:
            regs = read_raw_regs(unit_id)
            h, t = to_humi_temp(regs)
            dp = dew_point_c(t, h)
            out[label] = {
                "raw": regs,
                "humi": round(h, 1),
                "temp": round(t, 1),
                "dewpoint": round(dp, 1),
                "unit_id": unit_id,
            }
        except Exception as e:
            out[label] = {"unit_id": unit_id, "error": str(e)}
    return out

@router.get("/sensor/{unit_id}")
def read_sensor_unit(unit_id: int):
    regs = read_raw_regs(unit_id)
    h, t = to_humi_temp(regs)
    dp = dew_point_c(t, h)
    return {
        "raw": regs,
        "humi": round(h, 1),
        "temp": round(t, 1),
        "dewpoint": round(dp, 1),
        "unit_id": unit_id,
    }

@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)
