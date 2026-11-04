#!/usr/bin/env python3
"""CR300 -> CSV (+ optional InfluxDB) collector. Self-contained, dual-stream.

Two independent data streams in one process:
  - averaged : the logged table (Every60/Every30) -> data/csv/YYYY-MM-DD.csv
               deduped by RecNbr; polled every POLL_SECONDS
  - live     : the Public table (instantaneous values) -> data/public/YYYY-MM-DD.csv
               snapshotted every PUBLIC_POLL_SECONDS (set 0 to disable)

Runs with only pycampbellcr1000 + stdlib. InfluxDB optional (INFLUX_URL+TOKEN).

Config via env vars (all optional, sane defaults):
  CR300_URL=serial:/dev/cr300:115200
  CR300_TABLE=Every60          # averaged/logged table
  POLL_SECONDS=300             # how often to check the logged table
  PUBLIC_TABLE=Public          # live table (set PUBLIC_POLL_SECONDS=0 to disable)
  PUBLIC_POLL_SECONDS=60       # how often to snapshot live values
  BACKFILL_HOURS=6
  LOGGER_OFFSET_HOURS=-7       # CR300 clock is 7h fast; -7 -> true local time
  STATION=narit_cr300
  DATA_HOME=~/cr300_data
  INFLUX_URL= INFLUX_TOKEN= INFLUX_ORG= INFLUX_BUCKET= INFLUX_MEASUREMENT=
"""
import csv
import datetime as dt
import json
import math
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from pycampbellcr1000 import CR1000


def log(m):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {m}", flush=True)


URL = os.getenv("CR300_URL", "serial:/dev/cr300:115200")
TABLE = os.getenv("CR300_TABLE", "Every60")
POLL = int(os.getenv("POLL_SECONDS", "300"))
PUBLIC_TABLE = os.getenv("PUBLIC_TABLE", "Public")
PUBLIC_POLL = int(os.getenv("PUBLIC_POLL_SECONDS", "60"))
BACKFILL_H = int(os.getenv("BACKFILL_HOURS", "6"))
OFFSET = dt.timedelta(hours=float(os.getenv("LOGGER_OFFSET_HOURS", "-7")))
TZ = ZoneInfo(os.getenv("LOGGER_TZ", "Asia/Bangkok"))
UTC = ZoneInfo("UTC")
STATION = os.getenv("STATION", "narit_cr300")
# fields to drop entirely (e.g. a floating/unconnected channel like PPFD).
# comma-separated; matched against the cleaned column name.
IGNORE_FIELDS = {f.strip() for f in os.getenv("IGNORE_FIELDS", "").split(",") if f.strip()}

HOME = Path(os.path.expanduser(os.getenv("DATA_HOME", "~/cr300_data")))
CSV_DIR = HOME / "csv"
PUBLIC_DIR = HOME / "public"
STATE_FILE = HOME / "state.json"
LATEST_FILE = HOME / "latest.json"
LATEST_PUBLIC_FILE = HOME / "latest_public.json"
NODATA = -8190.0

# ---- optional InfluxDB ----
influx_write = None
if os.getenv("INFLUX_URL") and os.getenv("INFLUX_TOKEN"):
    try:
        from influxdb_client import InfluxDBClient, Point, WritePrecision
        from influxdb_client.client.write_api import SYNCHRONOUS
        _bucket = os.getenv("INFLUX_BUCKET", "cr300")
        _meas = os.getenv("INFLUX_MEASUREMENT", "weather")
        _api = InfluxDBClient(
            url=os.getenv("INFLUX_URL"), token=os.getenv("INFLUX_TOKEN"),
            org=os.getenv("INFLUX_ORG", "narit"),
        ).write_api(write_options=SYNCHRONOUS)

        def influx_write(fields, utc_time, stream):
            p = Point(_meas).tag("station", STATION).tag("stream", stream)
            p.time(utc_time, WritePrecision.S)
            for k, v in fields.items():
                p.field(k, float(v))
            _api.write(bucket=_bucket, record=p)

        log("InfluxDB enabled")
    except Exception as e:
        log(f"InfluxDB disabled ({e}); CSV only")
else:
    log("InfluxDB not configured; CSV only")


# ---- helpers ----
def ckey(k):
    if isinstance(k, bytes):
        return k.decode(errors="ignore")
    k = str(k)
    if (k.startswith("b'") and k.endswith("'")) or (k.startswith('b"') and k.endswith('"')):
        return k[2:-1]
    return k


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def good(v):
    return is_num(v) and not (isinstance(v, float) and math.isnan(v)) and v != NODATA


def rec_nbr(d):
    for key in ("RecNbr", "RecNum"):
        if key in d:
            try:
                return int(d[key])
            except (TypeError, ValueError):
                pass
    return -1


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_rn": -1, "last_logger_time": None, "last_public_time": None}


# ---- device ops with retry (handles transient PakBus 8-byte blips) ----
dev = None


def connect(n=8):
    global dev
    for i in range(n):
        try:
            dev = CR1000.from_url(URL)
            return dev
        except Exception as e:
            log(f"  connect {i+1}: {type(e).__name__}: {e}")
            time.sleep(2)
    raise RuntimeError("connect failed")


def op(fn, tries=4):
    global dev
    last = None
    for i in range(tries):
        try:
            if dev is None:
                connect()
            return fn(dev)
        except Exception as e:
            last = e
            log(f"  op {i+1}: {type(e).__name__}: {e}")
            dev = None
            time.sleep(2)
    raise RuntimeError(f"op failed: {last}")


def rnd(v):
    return round(v, 4) if isinstance(v, float) else v


def clean_row(raw):
    """-> (recnbr, logger_dt, local_dt, cleaned_row_dict, numeric_fields_dict)."""
    row = {ckey(k): v for k, v in raw.items()}
    rn = rec_nbr(row)
    ldt = row.get("Datetime")
    local = (ldt + OFFSET) if isinstance(ldt, dt.datetime) else None
    fields = {k: rnd(v) for k, v in row.items()
              if k not in ("Datetime", "RecNbr", "RecNum")
              and k not in IGNORE_FIELDS and good(v)}
    return rn, ldt, local, row, fields


def write_csv(directory, rn, local, row):
    day = local.strftime("%Y-%m-%d") if local else "unknown"
    path = directory / f"{day}.csv"
    out = {"TimeStamp": local.strftime("%Y-%m-%d %H:%M:%S") if local else "",
           "RecNbr": rn}
    for k, v in row.items():
        if k in ("Datetime", "RecNbr", "RecNum") or k in IGNORE_FIELDS:
            continue
        if isinstance(v, (bytes, bytearray)):
            continue  # skip binary fields (e.g. CVMeta)
        if isinstance(v, float):
            v = "" if math.isnan(v) else round(v, 4)
        out[k] = v
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out.keys()), extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(out)


def write_latest(path, rn, local, fields):
    path.write_text(json.dumps({
        "station": STATION, "RecNbr": rn,
        "time_local": local.strftime("%Y-%m-%d %H:%M:%S") if local else None,
        "fields": fields,
    }, default=str))


# ---- stream 1: averaged / logged table ----
def poll_averaged(st):
    logger_now = op(lambda d: d.gettime())
    if st["last_logger_time"]:
        start = dt.datetime.fromisoformat(st["last_logger_time"])
    else:
        start = logger_now - dt.timedelta(hours=BACKFILL_H)
    raws = op(lambda d: d.get_data(TABLE, start, logger_now + dt.timedelta(minutes=1)))
    new = [clean_row(r) for r in raws]
    new = [x for x in new if x[0] > st["last_rn"]]
    new.sort(key=lambda x: x[0])
    for rn, ldt, local, row, fields in new:
        write_csv(CSV_DIR, rn, local, row)
        if influx_write and local and fields:
            try:
                influx_write(fields, local.replace(tzinfo=TZ).astimezone(UTC), "averaged")
            except Exception as e:
                log(f"  influx(avg): {e}")
        st["last_rn"] = rn
        if isinstance(ldt, dt.datetime):
            st["last_logger_time"] = ldt.isoformat()
    if new:
        rn, ldt, local, row, fields = new[-1]
        write_latest(LATEST_FILE, rn, local, fields)
        STATE_FILE.write_text(json.dumps(st))
        log(f"[avg] wrote {len(new)} row(s), last RecNbr={st['last_rn']} local={local}")
    else:
        log("[avg] no new rows")


# ---- stream 2: live / Public table snapshot ----
def read_public(d):
    """Read the current Public record via collect-all (mode 0x03).

    get_data() uses a time-range collect that returns nothing for Public
    (it stores only the current scan, not a time series). collect-all returns
    that one current record. Returns a raw dict compatible with clean_row().
    """
    d.ping_node()
    data, _more = d._collect_data(PUBLIC_TABLE)
    best = None
    for rec in data:
        if not rec.get("NbrOfRecs"):
            continue
        for item in rec.get("RecFrag", []):
            best = item
    if best is None:
        return None
    raw = {"Datetime": best.get("TimeOfRec"), "RecNbr": best.get("RecNbr")}
    raw.update(dict(best.get("Fields", {})))
    return raw


def poll_public(st):
    raw = op(lambda d: read_public(d))
    if not raw:
        log("[live] empty read")
        return
    rn, ldt, local, row, fields = clean_row(raw)
    # timestamp: prefer the record's own, else the logger clock (+offset)
    if local is None:
        local = op(lambda d: d.gettime()) + OFFSET
    stamp = local.strftime("%Y-%m-%d %H:%M:%S")
    if st.get("last_public_time") == stamp:
        log("[live] no change")
        return
    write_csv(PUBLIC_DIR, rn, local, row)
    if influx_write and fields:
        try:
            influx_write(fields, local.replace(tzinfo=TZ).astimezone(UTC), "live")
        except Exception as e:
            log(f"  influx(live): {e}")
    write_latest(LATEST_PUBLIC_FILE, rn, local, fields)
    st["last_public_time"] = stamp
    STATE_FILE.write_text(json.dumps(st))
    log(f"[live] snapshot local={local} fields={list(fields)[:6]}...")


def main():
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    if PUBLIC_POLL > 0:
        PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    st = load_state()
    log(f"start URL={URL} avg_table={TABLE} poll={POLL}s "
        f"public={'off' if PUBLIC_POLL <= 0 else PUBLIC_TABLE + '@' + str(PUBLIC_POLL) + 's'} "
        f"offset={OFFSET} data={HOME}")

    next_avg = 0.0  # due immediately
    next_pub = 0.0
    while True:
        now = time.monotonic()
        if now >= next_avg:
            try:
                poll_averaged(st)
            except Exception as e:
                log(f"[avg] cycle error: {type(e).__name__}: {e}")
            next_avg = time.monotonic() + POLL
        if PUBLIC_POLL > 0 and time.monotonic() >= next_pub:
            try:
                poll_public(st)
            except Exception as e:
                log(f"[live] cycle error: {type(e).__name__}: {e}")
            next_pub = time.monotonic() + PUBLIC_POLL
        # sleep until the next due task
        upcoming = [next_avg] + ([next_pub] if PUBLIC_POLL > 0 else [])
        time.sleep(max(1.0, min(upcoming) - time.monotonic()))


if __name__ == "__main__":
    main()
