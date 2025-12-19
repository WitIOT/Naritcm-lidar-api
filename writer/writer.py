import os
import time
import math
import requests
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient, Point, WritePrecision

SENSOR_API_URL = os.getenv("SENSOR_API_URL", "http://naritcm-lidar-api:8000/api/sensor")
POLL_SEC = float(os.getenv("POLL_SEC", "1.0"))
TIMEOUT_SEC = float(os.getenv("HTTP_TIMEOUT_SEC", "2.5"))

INFLUX_URL = os.getenv("INFLUX_URL", "http://192.168.49.8:9086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "Narit")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "Lidar")
MEASUREMENT = os.getenv("MEASUREMENT", "room1")

def dewpoint_c(temp_c: float, rh: float) -> float:
    # Magnus formula
    a, b = 17.62, 243.12
    rh = max(0.1, min(100.0, float(rh)))
    t = float(temp_c)
    gamma = math.log(rh / 100.0) + (a * t) / (b + t)
    return (b * gamma) / (a - gamma)

def now_ns() -> int:
    return int(time.time() * 1e9)

def fetch_sensor() -> dict:
    r = requests.get(SENSOR_API_URL, timeout=TIMEOUT_SEC)
    r.raise_for_status()
    return r.json()

def main():
    if not INFLUX_TOKEN:
        raise SystemExit("INFLUX_TOKEN is empty. Please set it in docker-compose.yml environment.")

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api()

    backoff = 1.0
    while True:
        try:
            data = fetch_sensor()

            ts = now_ns()
            points = []

            for loc in ("indoor", "outdoor"):
                d = data.get(loc) or {}
                if not isinstance(d, dict):
                    continue

                # รองรับทั้ง format ใหม่ที่มี dewpoint และ format เก่าที่ไม่มี
                temp = d.get("temp")
                humi = d.get("humi")

                if isinstance(temp, (int, float)) and isinstance(humi, (int, float)):
                    dp = d.get("dewpoint")
                    if not isinstance(dp, (int, float)):
                        dp = dewpoint_c(temp, humi)

                    p = (
                        Point(MEASUREMENT)
                        .tag("location", loc)
                        .field("temp", float(temp))
                        .field("humi", float(humi))
                        .field("dewpoint", float(dp))
                        .time(ts, WritePrecision.NS)
                    )
                    points.append(p)

            if points:
                write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)

            backoff = 1.0
            time.sleep(POLL_SEC)

        except Exception as e:
            # กันล้ม: ถ้า API หรือ Influx มีปัญหา จะหน่วงแล้วลองใหม่
            time.sleep(backoff)
            backoff = min(backoff * 2.0, 30.0)

if __name__ == "__main__":
    main()