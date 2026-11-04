"""CR300 read-only REST endpoints, served by the lidar FastAPI app.

The cr300-collector service writes CSV + latest JSON to a shared volume
(CR300_DATA_HOME, default /data/cr300). This router just reads those files,
so it needs no serial device and no extra Python deps.

Mounted into main_2.py with:  app.include_router(cr300_router)

Endpoints (all under /api/cr300):
  GET /api/cr300/health
  GET /api/cr300/latest                 -> newest averaged record
  GET /api/cr300/live                   -> newest live (Public) snapshot
  GET /api/cr300/records?hours=24       -> averaged rows, last N hours
  GET /api/cr300/records?date=YYYY-MM-DD
  GET /api/cr300/records?stream=live&...
  GET /api/cr300/records?...&raw=1       -> keep -8190 / NAN values
  GET /api/cr300/fields?stream=averaged|live
  GET /api/cr300/days?stream=averaged|live
  GET /api/cr300/csv/{date}?stream=averaged|live   -> raw CSV download
"""
import csv
import datetime as dt
import json
import os
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, PlainTextResponse

HOME = Path(os.getenv("CR300_DATA_HOME", "/data/cr300"))
DIRS = {"averaged": HOME / "csv", "live": HOME / "public"}
LATEST = {"averaged": HOME / "latest.json", "live": HOME / "latest_public.json"}
STATION = os.getenv("STATION", "narit_cr300")
NODATA = "-8190.0"

router = APIRouter(prefix="/api/cr300", tags=["cr300"])


def _stream(s: str) -> str:
    return s if s in DIRS else "averaged"


def _day_files(stream: str):
    d = DIRS[stream]
    return sorted(d.glob("*.csv")) if d.exists() else []


def _read_day(stream: str, date_str: str, drop_nan: bool = True):
    path = DIRS[stream] / f"{date_str}.csv"
    if not path.exists():
        return []
    rows = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            if drop_nan:
                r = {k: v for k, v in r.items()
                     if v not in ("", NODATA, "nan", "-8190", "-8190.00")}
            rows.append(r)
    return rows


def _parse_ts(r):
    try:
        return dt.datetime.strptime(r.get("TimeStamp", ""), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


@router.get("/health")
def cr300_health():
    return {"ok": True, "station": STATION,
            "now": dt.datetime.now().isoformat(),
            "averaged_days": len(_day_files("averaged")),
            "live_days": len(_day_files("live"))}


@router.get("/latest")
def cr300_latest():
    f = LATEST["averaged"]
    if f.exists():
        return json.loads(f.read_text())
    return JSONResponse(status_code=503, content={"ok": False, "error": "no data yet"})


@router.get("/live")
def cr300_live():
    f = LATEST["live"]
    if f.exists():
        return json.loads(f.read_text())
    return JSONResponse(status_code=503, content={"ok": False, "error": "no live data yet"})


@router.get("/days")
def cr300_days(stream: str = "averaged"):
    s = _stream(stream)
    return {"stream": s, "days": [p.stem for p in _day_files(s)]}


@router.get("/fields")
def cr300_fields(stream: str = "averaged"):
    s = _stream(stream)
    flds = set()
    for p in _day_files(s):
        with p.open(newline="") as f:
            flds.update(next(csv.reader(f), []))
    return {"stream": s, "fields": sorted(flds)}


@router.get("/records")
def cr300_records(
    hours: int = Query(24, ge=1, le=24 * 60),
    date: str | None = None,
    stream: str = "averaged",
    raw: int = 0,
):
    s = _stream(stream)
    drop = raw not in (1,)
    if date:
        rows = _read_day(s, date, drop)
    else:
        cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
        rows = []
        for p in _day_files(s)[-3:]:
            for r in _read_day(s, p.stem, drop):
                ts = _parse_ts(r)
                if ts and ts >= cutoff:
                    rows.append(r)
        rows.sort(key=lambda r: r.get("TimeStamp", ""))
    return {"ok": True, "station": STATION, "stream": s,
            "count": len(rows), "records": rows}


@router.get("/csv/{date}")
def cr300_csv(date: str, stream: str = "averaged"):
    s = _stream(stream)
    p = DIRS[s] / f"{date}.csv"
    if p.exists() and p.parent == DIRS[s]:
        return PlainTextResponse(p.read_text(), media_type="text/csv")
    return JSONResponse(status_code=404, content={"ok": False, "error": "not found"})
