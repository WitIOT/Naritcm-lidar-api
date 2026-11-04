import os
import time
import math
import requests
from datetime import datetime, timezone, timedelta

from influxdb_client import InfluxDBClient, Point, WritePrecision

# ===== Sensor API =====
SENSOR_API_URL = os.getenv("SENSOR_API_URL", "http://naritcm-lidar-api:8000/api/sensor")
RAIN_API_URL   = os.getenv("RAIN_API_URL",   "http://naritcm-lidar-api:8000/api/rain")
# CR300 weather (live = realtime Public, latest = hourly averaged)
CR300_LIVE_URL   = os.getenv("CR300_LIVE_URL",   "http://naritcm-lidar-api:8000/api/cr300/live")
CR300_LATEST_URL = os.getenv("CR300_LATEST_URL", "http://naritcm-lidar-api:8000/api/cr300/latest")
POLL_SEC       = float(os.getenv("POLL_SEC",          "1.0"))
TIMEOUT_SEC    = float(os.getenv("HTTP_TIMEOUT_SEC",  "2.5"))

# ===== InfluxDB =====
INFLUX_URL    = os.getenv("INFLUX_URL",    "http://192.168.49.8:9086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN",  "")
INFLUX_ORG    = os.getenv("INFLUX_ORG",   "Narit")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "Lidar")
MEASUREMENT   = os.getenv("MEASUREMENT",   "room1")
RAIN_MEASUREMENT = os.getenv("RAIN_MEASUREMENT", "rain")  # measurement สำหรับฝน
WEATHER_MEASUREMENT = os.getenv("WEATHER_MEASUREMENT", "weather")  # measurement สำหรับ CR300
# เวลาจาก CR300 เป็นเวลาท้องถิ่น (Asia/Bangkok = UTC+7, ไม่มี DST) -> แปลงเป็น UTC
WEATHER_TZ_OFFSET_H = float(os.getenv("WEATHER_TZ_OFFSET_HOURS", "7"))

# จำ timestamp ล่าสุดที่เขียนไปแล้วของแต่ละ stream กันเขียนซ้ำ
_cr300_last = {"live": None, "averaged": None}


# =========================================================
# Helpers
# =========================================================
def dewpoint_c(temp_c: float, rh: float) -> float:
    a, b = 17.62, 243.12
    rh = max(0.1, min(100.0, float(rh)))
    t = float(temp_c)
    gamma = math.log(rh / 100.0) + (a * t) / (b + t)
    return (b * gamma) / (a - gamma)


def now_ns() -> int:
    return int(time.time() * 1e9)


def local_to_utc_ns(time_local: str) -> int:
    """'2026-11-04 12:00:00' (Thai local) -> UTC epoch ns."""
    dt_local = datetime.strptime(time_local, "%Y-%m-%d %H:%M:%S")
    dt_utc = dt_local - timedelta(hours=WEATHER_TZ_OFFSET_H)
    return int(dt_utc.replace(tzinfo=timezone.utc).timestamp() * 1e9)


def fetch_json(url: str, timeout: float) -> dict:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


# =========================================================
# Build Points
# =========================================================
def build_sensor_points(data: dict, ts: int) -> list:
    """สร้าง InfluxDB points จาก /api/sensor (indoor/outdoor)"""
    points = []
    for loc in ("indoor", "outdoor"):
        d = data.get(loc) or {}
        if not isinstance(d, dict):
            continue
        temp = d.get("temp")
        humi = d.get("humi")
        if not isinstance(temp, (int, float)) or not isinstance(humi, (int, float)):
            continue
        dp = d.get("dewpoint")
        if not isinstance(dp, (int, float)):
            dp = dewpoint_c(temp, humi)
        p = (
            Point(MEASUREMENT)
            .tag("location", loc)
            .field("temp",     float(temp))
            .field("humi",     float(humi))
            .field("dewpoint", float(dp))
            .time(ts, WritePrecision.NS)
        )
        points.append(p)
    return points


def build_rain_points(data: dict, ts: int) -> list:
    """สร้าง InfluxDB points จาก /api/rain"""
    points = []

    if not data.get("online", False):
        return points

    intensity   = data.get("intensity",    {})
    accum       = data.get("accumulation", {})
    lens        = data.get("lens",         {})

    rint        = intensity.get("value")
    event_acc   = (accum.get("event") or {}).get("value")
    total_acc   = (accum.get("total") or {}).get("value")
    is_raining  = data.get("is_raining", False)
    em_total    = lens.get("em_total")
    lens_status = lens.get("status", "unknown")

    if rint is None or event_acc is None or total_acc is None:
        return points

    p = (
        Point(RAIN_MEASUREMENT)
        .tag("sensor", "rg15")
        .field("rint",       float(rint))
        .field("event_acc",  float(event_acc))
        .field("total_acc",  float(total_acc))
        .field("is_raining", int(is_raining))
        .field("lens_status", lens_status)
        .time(ts, WritePrecision.NS)
    )

    if isinstance(em_total, int):
        p = p.field("em_total", em_total)

    points.append(p)
    return points


def build_cr300_points(data: dict, stream: str) -> list:
    """สร้าง points จาก /api/cr300/{live,latest}. เขียนเฉพาะเมื่อ record ใหม่
    (dedup ด้วย time_local) ใช้ time_local เป็น timestamp ของ point."""
    points = []
    if not isinstance(data, dict):
        return points
    tl = data.get("time_local")
    fields = data.get("fields") or {}
    if not tl or not isinstance(fields, dict):
        return points
    if _cr300_last.get(stream) == tl:   # record เดิม ไม่ต้องเขียนซ้ำ
        return points
    try:
        ts = local_to_utc_ns(tl)
    except Exception:
        ts = now_ns()
    p = Point(WEATHER_MEASUREMENT).tag("station", data.get("station", "narit_cr300")).tag("stream", stream)
    nfields = 0
    for k, v in fields.items():
        if isinstance(v, bool):
            p = p.field(k, int(v)); nfields += 1
        elif isinstance(v, (int, float)):
            p = p.field(k, float(v)); nfields += 1
    if nfields == 0:
        return points
    if data.get("RecNbr") is not None:
        try:
            p = p.field("RecNbr", int(data["RecNbr"]))
        except (TypeError, ValueError):
            pass
    p = p.time(ts, WritePrecision.NS)
    points.append(p)
    _cr300_last[stream] = tl   # จำไว้ว่าเขียน record นี้แล้ว
    return points


# =========================================================
# Main loop
# =========================================================
def main():
    if not INFLUX_TOKEN:
        raise SystemExit("INFLUX_TOKEN is empty. Please set it in .env / environment.")

    client    = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api()

    backoff = 1.0

    while True:
        ts = now_ns()
        points = []
        errors = []

        # ---- sensor (indoor/outdoor) ----
        try:
            points += build_sensor_points(fetch_json(SENSOR_API_URL, TIMEOUT_SEC), ts)
        except Exception as e:
            errors.append(f"sensor: {e}")

        # ---- rain (RG-15) ----
        try:
            points += build_rain_points(fetch_json(RAIN_API_URL, TIMEOUT_SEC), ts)
        except Exception as e:
            errors.append(f"rain: {e}")

        # ---- CR300 weather: live (realtime) + averaged (hourly) ----
        try:
            points += build_cr300_points(fetch_json(CR300_LIVE_URL, TIMEOUT_SEC), "live")
        except Exception as e:
            errors.append(f"cr300 live: {e}")
        try:
            points += build_cr300_points(fetch_json(CR300_LATEST_URL, TIMEOUT_SEC), "averaged")
        except Exception as e:
            errors.append(f"cr300 avg: {e}")

        # ---- write ----
        if points:
            try:
                write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)
                backoff = 1.0
            except Exception as e:
                errors.append(f"influx write: {e}")
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

        if errors:
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)
        else:
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
