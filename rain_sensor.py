"""
rain_sensor.py
RG-15 Optical Rain Gauge - Serial Reader (USB TTL / CH340)
"""
import os
import re
import time
import threading
import serial
from datetime import datetime, timezone
from collections import deque
from typing import Optional

# ===== Config from environment =====
RAIN_PORT     = os.getenv("RAIN_PORT",      "/dev/ttyUSB0")
RAIN_BAUDRATE = int(os.getenv("RAIN_BAUDRATE", "9600"))
RAIN_POLL_S   = float(os.getenv("RAIN_POLL_S",  "3.0"))
RAIN_HISTORY  = int(os.getenv("RAIN_HISTORY",   "100"))
LENS_POLL_N   = int(os.getenv("LENS_POLL_N",    "60"))   # ส่ง K ทุก N รอบ (~3 นาที)


# =========================================================
# State
# =========================================================
class RainSensorState:
    def __init__(self):
        self._lock = threading.Lock()
        self.is_raining:   bool  = False
        self.rint:         float = 0.0   # mm/hr
        self.event_acc:    float = 0.0   # mm (สะสมครั้งนี้)
        self.total_acc:    float = 0.0   # mm (สะสมทั้งหมด)
        self.em_total:     Optional[int] = None
        self.lens_bad:     bool  = False
        self.em_sat:       bool  = False
        self.lens_status:  str   = "unknown"
        self.online:       bool  = False
        self.last_update:  Optional[str] = None
        self._history:     deque = deque(maxlen=RAIN_HISTORY)

    # ---- writers ----
    def update_rain(self, rint: float, event: float, total: float):
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()  # timestamp ภายใต้ lock — สอดคล้องกับข้อมูล
            self.rint       = rint
            self.event_acc  = event
            self.total_acc  = total
            self.is_raining = rint > 0
            self.online     = True
            self.last_update = now
            self._history.append({
                "timestamp": now,
                "rint":      round(rint,  1),
                "event_acc": round(event, 1),
                "total_acc": round(total, 1),
                "unit":      "mm",
            })

    def update_lens(self, em_total: Optional[int], lens_bad: bool, em_sat: bool):
        with self._lock:
            if em_total is not None:
                self.em_total = em_total
            if lens_bad:
                self.lens_bad = True
            if em_sat:
                self.em_sat = em_sat
            # resolve status
            if self.lens_bad:
                self.lens_status = "bad"
            elif self.em_sat:
                self.lens_status = "saturated"
            elif self.em_total is not None and self.em_total < 10:
                self.lens_status = "dirty"
            elif self.em_total is not None:
                self.lens_status = "ok"

    def set_offline(self):
        with self._lock:
            self.online = False

    # ---- readers ----
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "online":      self.online,
                "last_update": self.last_update,
                "is_raining":  self.is_raining,
                "intensity":   {"value": round(self.rint,       1), "unit": "mm/hr"},
                "accumulation": {
                    "event": {"value": round(self.event_acc, 1), "unit": "mm"},
                    "total": {"value": round(self.total_acc, 1), "unit": "mm"},
                },
                "lens": {
                    "status":   self.lens_status,
                    "em_total": self.em_total,
                    "lens_bad": self.lens_bad,
                    "em_sat":   self.em_sat,
                },
            }

    def history_snapshot(self) -> list:
        with self._lock:
            return list(self._history)


# Global singleton
rain_state = RainSensorState()


# =========================================================
# Helpers
# =========================================================

# Pre-compile regex — กัน re.compile ทุก call (เรียกทุก 0.8s)
_RE_EVENT = re.compile(r"EventAcc\s+([\d.]+)")
_RE_TOTAL = re.compile(r"TotalAcc\s+([\d.]+)")
_RE_RINT  = re.compile(r"RInt\s+([\d.]+)")
_RE_EMTOT = re.compile(r"EmTotal\s+(\d+)")


def _parse_rain(line: str):
    """คืน (rint, event, total) หรือ None"""
    if "Acc" not in line:
        return None
    try:
        event = float(_RE_EVENT.search(line).group(1))
        total = float(_RE_TOTAL.search(line).group(1))
        rint  = float(_RE_RINT.search(line).group(1))
        return rint, event, total
    except Exception:
        return None


def _parse_lens(line: str):
    """คืน (em_total | None, lens_bad, em_sat)"""
    m = _RE_EMTOT.search(line)
    em_total = int(m.group(1)) if m else None
    return em_total, ("LensBad" in line), ("EmSat" in line)


# =========================================================
# Background polling thread
# =========================================================
def _poll_loop():
    count = 0
    ser: Optional[serial.Serial] = None

    while True:
        # ---- open serial if needed ----
        try:
            if ser is None or not ser.is_open:
                ser = serial.Serial(
                    port=RAIN_PORT,
                    baudrate=RAIN_BAUDRATE,
                    timeout=2,
                )
                time.sleep(0.3)
        except serial.SerialException:
            rain_state.set_offline()
            time.sleep(5)
            continue

        # ---- send command ----
        try:
            cmd = b"K\n" if count % LENS_POLL_N == 0 else b"R\n"
            ser.write(cmd)
            count += 1
        except Exception:
            rain_state.set_offline()
            _close(ser)
            ser = None
            time.sleep(5)
            continue

        # ---- read response until next poll ----
        deadline = time.time() + RAIN_POLL_S
        try:
            while time.time() < deadline:
                raw = ser.readline()
                if not raw:
                    break
                line = raw.decode("ascii", errors="ignore").strip()
                if not line:
                    continue

                # rain data
                parsed = _parse_rain(line)
                if parsed:
                    rain_state.update_rain(*parsed)

                # lens data
                em_total, lens_bad, em_sat = _parse_lens(line)
                if em_total is not None or lens_bad or em_sat:
                    rain_state.update_lens(em_total, lens_bad, em_sat)

        except serial.SerialException:
            rain_state.set_offline()
            _close(ser)
            ser = None
            time.sleep(5)
        except Exception:
            rain_state.set_offline()
            time.sleep(2)


def _close(ser):
    try:
        if ser and ser.is_open:
            ser.close()
    except Exception:
        pass


def start_rain_polling():
    """เรียกครั้งเดียวตอน startup"""
    t = threading.Thread(target=_poll_loop, daemon=True, name="rain-poll")
    t.start()
