import os
import time
import math
import asyncio
import threading
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any, Literal, Set

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from periphery import GPIO
from pymodbus.client import ModbusSerialClient

from rain_sensor import rain_state, start_rain_polling

# =========================================================
# App
# =========================================================
app = FastAPI(
    title="NARIT CM LiDAR API (Door + Limit + RS485 Sensor + Rain)",
    version="3.2"
)

# =========================================================
# Static
# =========================================================
STATIC_DIR = os.getenv("STATIC_DIR", "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return {"ok": True, "service": "naritcm-lidar-api", "docs": "/docs"}


# =========================================================
# Health
# =========================================================
@app.get("/health")
def health():
    return {"ok": True, "service": "naritcm-lidar-api", "version": "3.2"}


# =========================================================
# Door + Limit
# =========================================================
GPIO_CHIP = os.getenv("GPIO_CHIP", "/dev/gpiochip0")

LINE_OPEN  = int(os.getenv("LINE_OPEN",  "25"))
LINE_CLOSE = int(os.getenv("LINE_CLOSE", "24"))
DEFAULT_PULSE_MS = int(os.getenv("DEFAULT_PULSE_MS", "800"))

LINE_DI1 = int(os.getenv("LINE_DI1", "17"))
DI1_ACTIVE_HIGH = os.getenv("DI1_ACTIVE_HIGH", "true").lower() == "true"
DI1_DEBOUNCE_MS = int(os.getenv("DI1_DEBOUNCE_MS", "50"))


class DOManager:
    def __init__(self, chip_path: str, line_open: int, line_close: int):
        self.lock = threading.Lock()
        self.state: Literal[
            "idle", "opening", "closing", "holding_open", "holding_close"
        ] = "idle"
        self.gpio_open = GPIO(chip_path, line_open, "out")
        self.gpio_close = GPIO(chip_path, line_close, "out")
        self.all_low()

    def all_low(self):
        self.gpio_open.write(False)
        self.gpio_close.write(False)

    def pulse(self, target: Literal["open", "close"], ms: int):
        """
        ส่ง pulse แบบ non-blocking — spawn thread แยก แล้วคืนทันที
        HTTP handler ไม่ถูกบล็อกตลอด pulse duration (สูงสุด 5 วินาที)
        """
        if not (1 <= ms <= 5000):
            raise ValueError("pulse ms must be 1..5000")

        def _do_pulse():
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

        threading.Thread(target=_do_pulse, daemon=True, name=f"pulse-{target}").start()

    def hold(self, target: Literal["open", "close"]):
        with self.lock:
            self.all_low()
            if target == "open":
                self.gpio_open.write(True)
                self.state = "holding_open"
            else:
                self.gpio_close.write(True)
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


class LimitCache:
    """
    อ่าน GPIO DI1 ใน background thread ทุก poll_ms ms
    endpoint /limit/status คืนจาก cache → ไม่ blocking main thread
    """
    def __init__(self, reader: "DIReader", poll_ms: int = 200):
        self._reader = reader
        self._poll_s = max(poll_ms, 50) / 1000.0
        self._state: bool = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="limit-poll")
        self._thread.start()

    def _run(self):
        while True:
            try:
                val = self._reader.read()
                with self._lock:
                    self._state = val
            except Exception:
                pass
            time.sleep(self._poll_s)

    def get(self) -> bool:
        with self._lock:
            return self._state


manager = DOManager(GPIO_CHIP, LINE_OPEN, LINE_CLOSE)
di1 = DIReader(GPIO_CHIP, LINE_DI1, DI1_ACTIVE_HIGH, DI1_DEBOUNCE_MS)
limit_cache = LimitCache(di1, poll_ms=200)  # อ่าน GPIO ทุก 200ms ใน background


class PulseRequest(BaseModel):
    ms: Optional[int] = None


@app.get("/door/status")
def door_status():
    return {"ok": True, "status": manager.status()}


@app.post("/door/open")
def door_open(
    body: Optional[PulseRequest] = None,
    ms: int = Query(None, ge=1, le=5000)
):
    pulse_ms = ms or (body.ms if body and body.ms else None) or DEFAULT_PULSE_MS
    try:
        manager.pulse("open", pulse_ms)
        return {"ok": True, "action": "open", "pulse_ms": pulse_ms}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/door/close")
def door_close(
    body: Optional[PulseRequest] = None,
    ms: int = Query(None, ge=1, le=5000)
):
    pulse_ms = ms or (body.ms if body and body.ms else None) or DEFAULT_PULSE_MS
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
    state = limit_cache.get()   # คืนจาก cache ทันที ไม่ blocking
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


# =========================================================
# Sensor RS485
# =========================================================
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUDRATE = int(os.getenv("BAUDRATE", "9600"))
PARITY = os.getenv("PARITY", "N")
BYTESIZE = int(os.getenv("BYTESIZE", "8"))
STOPBITS = int(os.getenv("STOPBITS", "1"))
TIMEOUT_S = float(os.getenv("TIMEOUT_S", "1.0"))

READ_TABLE = os.getenv("READ_TABLE", "holding").lower()
REG_START = int(os.getenv("REG_START", "0"))
REG_COUNT = int(os.getenv("REG_COUNT", "2"))

TEMP_INDEX = int(os.getenv("TEMP_INDEX", "1"))
HUMI_INDEX = int(os.getenv("HUMI_INDEX", "0"))
SCALE_DIV = float(os.getenv("SCALE_DIV", "10"))

INDOOR_ID = int(os.getenv("INDOOR_ID", "1"))
OUTDOOR_ID = int(os.getenv("OUTDOOR_ID", "2"))

POLL_MS = int(os.getenv("POLL_MS", "1000"))

modbus = ModbusSerialClient(
    port=SERIAL_PORT,
    baudrate=BAUDRATE,
    bytesize=BYTESIZE,
    parity=PARITY,
    stopbits=STOPBITS,
    timeout=TIMEOUT_S,
)

# RLock แทน Lock — กัน deadlock เมื่อ ensure_connected และ read_raw_regs ใช้ lock เดียวกัน
_modbus_lock = threading.RLock()


def read_raw_regs(unit_id: int) -> Tuple[int, ...]:
    """อ่าน registers พร้อม auto-reconnect ภายใต้ lock เดียว (ไม่ deadlock)"""
    with _modbus_lock:
        # ถ้าไม่ connected ให้ close ก่อนแล้วค่อย connect ใหม่
        # (กัน pymodbus คิดว่า connected แต่ socket จริงตายแล้ว)
        if not modbus.connected:
            try:
                modbus.close()
            except Exception:
                pass
            if not modbus.connect():
                raise RuntimeError("Modbus not connected")

        if READ_TABLE == "input":
            rr = modbus.read_input_registers(REG_START, REG_COUNT, slave=unit_id)
        else:
            rr = modbus.read_holding_registers(REG_START, REG_COUNT, slave=unit_id)

        if rr.isError():
            # force disconnect เพื่อให้ครั้งถัดไป reconnect ใหม่
            try:
                modbus.close()
            except Exception:
                pass
            raise RuntimeError(f"Modbus Error: {rr}")

    return tuple(rr.registers)


def to_humi_temp(regs):
    humi = regs[HUMI_INDEX] / SCALE_DIV
    temp = regs[TEMP_INDEX] / SCALE_DIV
    return humi, temp


def calc_dewpoint(temp_c: float, rh: float) -> float:
    if rh <= 0:
        return float("nan")
    a = 17.62
    b = 243.12
    gamma = math.log(rh / 100.0) + (a * temp_c) / (b + temp_c)
    return (b * gamma) / (a - gamma)


class SensorCache:
    """
    Poll Modbus ใน background thread ทุก poll_ms ms
    endpoints /api/sensor คืนจาก cache → ไม่ blocking, ไม่ชนกับ WebSocket poll loop
    """
    def __init__(self, poll_ms: int = 1000):
        self._poll_s = max(poll_ms, 200) / 1000.0
        self._lock = threading.Lock()
        self._data: Dict[int, Dict[str, Any]] = {}
        self._thread = threading.Thread(target=self._run, daemon=True, name="sensor-poll")
        self._thread.start()

    def _read_unit(self, unit_id: int) -> Dict[str, Any]:
        regs = read_raw_regs(unit_id)
        h, t = to_humi_temp(regs)
        d = calc_dewpoint(t, h)
        return {
            "unit_id":   unit_id,
            "raw":       regs,
            "temp":      round(t, 1),
            "humi":      round(h, 1),
            "dewpoint":  round(d, 1),
            "error":     None,
        }

    def _run(self):
        fail_count = 0
        while True:
            any_error = False
            for uid in (INDOOR_ID, OUTDOOR_ID):
                try:
                    entry = self._read_unit(uid)
                    with self._lock:
                        self._data[uid] = entry
                except Exception as e:
                    any_error = True
                    entry = {"unit_id": uid, "raw": None, "temp": None,
                             "humi": None, "dewpoint": None, "error": str(e)}
                    with self._lock:
                        self._data[uid] = entry

            if any_error:
                fail_count += 1
                # backoff: 1s → 2s → 4s → max 16s เมื่อ Modbus ไม่ตอบซ้ำ
                backoff = min(self._poll_s * (2 ** (fail_count - 1)), 16.0)
                time.sleep(backoff)
            else:
                fail_count = 0
                time.sleep(self._poll_s)

    def get(self, unit_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._data.get(unit_id)

    def get_all(self) -> Dict[int, Dict[str, Any]]:
        with self._lock:
            return dict(self._data)


sensor_cache = SensorCache(poll_ms=POLL_MS)


@app.get("/api/sensor/{unit_id}")
def read_sensor_unit(unit_id: int):
    entry = sensor_cache.get(unit_id)
    if entry is None:
        return JSONResponse(status_code=503, content={"ok": False, "unit_id": unit_id, "error": "No data yet"})
    if entry["error"]:
        return JSONResponse(status_code=500, content={"ok": False, "unit_id": unit_id, "error": entry["error"]})
    name = "indoor" if unit_id == INDOOR_ID else (
        "outdoor" if unit_id == OUTDOOR_ID else f"unit_{unit_id}"
    )
    return {
        "ok": True, "name": name, "unit_id": unit_id,
        "raw_registers": entry["raw"],
        "humi": entry["humi"], "temp": entry["temp"], "dewpoint": entry["dewpoint"],
    }


@app.get("/api/sensor")
def read_sensor_both():
    all_data = sensor_cache.get_all()
    out: Dict[str, Any] = {"ok": True}

    for uid, key in ((INDOOR_ID, "indoor"), (OUTDOOR_ID, "outdoor")):
        entry = all_data.get(uid)
        if entry is None:
            out[key] = {"unit_id": uid, "error": "No data yet"}
            out["ok"] = False
        elif entry["error"]:
            out[key] = {"unit_id": uid, "error": entry["error"]}
            out["ok"] = False
        else:
            out[key] = {
                "unit_id": uid, "raw": entry["raw"],
                "humi": entry["humi"], "temp": entry["temp"], "dewpoint": entry["dewpoint"],
            }

    return out


# =========================================================
# WebSocket Sensor Realtime
# =========================================================
ws_clients: Set[WebSocket] = set()
_ws_lock = asyncio.Lock()


@app.websocket("/ws/sensor")
async def ws_sensor(ws: WebSocket):
    await ws.accept()
    async with _ws_lock:
        ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            ws_clients.discard(ws)


async def sensor_poll_loop():
    """Push cache ไปยัง WebSocket clients ทุก POLL_MS — ไม่ยิง Modbus ซ้ำ"""
    while True:
        all_data = sensor_cache.get_all()
        payload: Dict[str, Any] = {"ts": int(time.time() * 1000), "ok": True}

        for uid, key in ((INDOOR_ID, "indoor"), (OUTDOOR_ID, "outdoor")):
            entry = all_data.get(uid)
            if entry and not entry["error"]:
                payload[key] = {
                    "unit_id":  uid,
                    "temp":     entry["temp"],
                    "humi":     entry["humi"],
                    "dewpoint": entry["dewpoint"],
                }
            else:
                payload["ok"] = False
                payload["error"] = (entry or {}).get("error", "No data")

        # snapshot set ภายใต้ lock กัน concurrent modification
        async with _ws_lock:
            clients = set(ws_clients)

        dead = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)

        if dead:
            async with _ws_lock:
                for ws in dead:
                    ws_clients.discard(ws)

        await asyncio.sleep(POLL_MS / 1000.0)


# =========================================================
# Rain Sensor RG-15
# =========================================================
@app.get("/api/rain")
def api_rain():
    """สถานะฝนปัจจุบัน พร้อม lens status"""
    data = rain_state.snapshot()
    return {
        "ok":        True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }


@app.get("/api/rain/history")
def api_rain_history(limit: int = Query(100, ge=1, le=1000)):
    """ประวัติการวัดฝนย้อนหลัง"""
    history = rain_state.history_snapshot()
    history = history[-limit:]
    return {
        "ok":        True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "count":     len(history),
        "history":   history,
    }


@app.get("/api/rain/lens")
def api_rain_lens():
    """สถานะ lens ของ RG-15"""
    data = rain_state.snapshot()
    return {
        "ok":        True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "online":    data["online"],
        "lens":      data["lens"],
    }


# =========================================================
# Startup / Shutdown
# =========================================================
@app.on_event("startup")
async def startup_event():
    start_rain_polling()
    asyncio.create_task(sensor_poll_loop())


@app.on_event("shutdown")
async def shutdown_event():
    try:
        modbus.close()
    except Exception:
        pass
