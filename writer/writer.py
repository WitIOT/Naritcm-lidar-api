import os
import time
import math
import requests
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision

# ===== Sensor API =====
SENSOR_API_URL = os.getenv("SENSOR_API_URL", "http://naritcm-lidar-api:8000/api/sensor")
RAIN_API_URL   = os.getenv("RAIN_API_URL",   "http://naritcm-lidar-api:8000/api/rain")
POLL_SEC       = float(os.getenv("POLL_SEC",          "1.0"))
TIMEOUT_SEC    = float(os.getenv("HTTP_TIMEOUT_SEC",  "2.5"))

# ===== InfluxDB =====
INFLUX_URL    = os.getenv("INFLUX_URL",    "http://192.168.49.8:9086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN",  "")
INFLUX_ORG    = os.getenv("INFLUX_ORG",   "Narit")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "Lidar")
MEASUREMENT   = os.getenv("MEASUREMENT",   "room1")
RAIN_MEASUREMENT = os.getenv("RAIN_MEASUREMENT", "rain")  # measurement สำหรับฝน


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

    # ตรวจสอบว่า online และมีข้อมูลครบ
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

    # ต้องมีค่าหลักครบจึงจะเขียน
    if rint is None or event_acc is None or total_acc is None:
        return points

    p = (
        Point(RAIN_MEASUREMENT)
        .tag("sensor", "rg15")
        .field("rint",       float(rint))
        .field("event_acc",  float(event_acc))
        .field("total_acc",  float(total_acc))
        .field("is_raining", int(is_raining))   # 1/0 (bool ไม่ support ใน InfluxDB line protocol)
        .field("lens_status", lens_status)
        .time(ts, WritePrecision.NS)
    )

    # เพิ่ม em_total ถ้ามีค่า
    if isinstance(em_total, int):
        p = p.field("em_total", em_total)

    points.append(p)
    return points


# =========================================================
# Main loop
# =========================================================
def main():
    if not INFLUX_TOKEN:
        raise SystemExit("INFLUX_TOKEN is empty. Please set it in docker-compose.yml environment.")

    client    = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api()

    backoff = 1.0

    while True:
        ts = now_ns()
        points = []
        errors = []

        # ---- อ่าน sensor (indoor/outdoor) ----
        try:
            sensor_data = fetch_json(SENSOR_API_URL, TIMEOUT_SEC)
            points += build_sensor_points(sensor_data, ts)
        except Exception as e:
            errors.append(f"sensor: {e}")

        # ---- อ่าน rain (RG-15) ----
        try:
            rain_data = fetch_json(RAIN_API_URL, TIMEOUT_SEC)
            points += build_rain_points(rain_data, ts)
        except Exception as e:
            errors.append(f"rain: {e}")

        # ---- เขียนเข้า InfluxDB ----
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
