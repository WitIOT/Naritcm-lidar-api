# Weather Station API (CR300)

REST API สำหรับข้อมูลสถานีตรวจอากาศ Campbell CR300 — เสิร์ฟโดย `naritcm-lidar-api`
บน **port 8000** เดียวกับ API ของ lidar (roof / sensor / rain)

- **Base URL:** `http://<host>:8000/api/cr300`
- **Format:** JSON (ยกเว้น `/csv/{date}` = text/csv)
- **CORS:** เปิด `Access-Control-Allow-Origin: *` — เรียกจาก browser/JS ได้
- **Swagger UI:** `http://<host>:8000/docs` (FastAPI สร้างอัตโนมัติ กดลองได้)

---

## แนวคิด — 2 สตรีม (stream)

| stream | ที่มา (CR300 table) | ความถี่ | เหมาะกับ |
|--------|---------------------|---------|----------|
| `averaged` *(default)* | `Every60` | ทุก 1 ชั่วโมง | แนวโน้ม/ค่าเฉลี่ย |
| `live` | `Public` | ทุก ~60 วินาที | ค่าเรียลไทม์ |

เวลา (`time_local` / `TimeStamp`) เป็น **เวลาไทย (Asia/Bangkok)** — ชดเชย −7 ชม. จากนาฬิกา CR300 แล้ว

---

## Endpoints

### `GET /health`
สถานะบริการ + จำนวนวันที่มีข้อมูล

**Response**
```json
{
  "ok": true,
  "station": "narit_cr300",
  "now": "2026-11-09T13:29:00",
  "averaged_days": 12,
  "live_days": 7
}
```

---

### `GET /latest`
record ล่าสุดของสตรีม **averaged** (รายชั่วโมง)

**Response `200`**
```json
{
  "station": "narit_cr300",
  "RecNbr": 1705,
  "time_local": "2026-11-09 13:00:00",
  "fields": {"BattV_Min": 12.36, "WindDir_D1_WVT": 0.0, "WindDir_SD1_WVT": 0.0}
}
```
**`503`** — ยังไม่มีข้อมูล: `{"ok": false, "error": "no data yet"}`

---

### `GET /live`
snapshot ล่าสุดของสตรีม **live** (เรียลไทม์)

**Response `200`**
```json
{
  "station": "narit_cr300",
  "RecNbr": 2161,
  "time_local": "2026-11-09 13:28:05",
  "fields": {"BattV": 12.3708, "PTemp_C": 26.56, "Invalid_Wind": 0}
}
```
**`503`** — ยังไม่มีข้อมูล: `{"ok": false, "error": "no live data yet"}`

---

### `GET /records`
ดึงหลาย record — เลือกช่วงแบบ `hours` หรือ `date`

**Query parameters**

| param | ชนิด | default | ความหมาย |
|-------|------|---------|----------|
| `hours` | int (1–1440) | 24 | ย้อนหลัง N ชั่วโมงจากปัจจุบัน |
| `date` | `YYYY-MM-DD` | — | เจาะวันเดียว (ถ้าใส่ จะใช้แทน `hours`) |
| `stream` | `averaged` \| `live` | `averaged` | เลือกสตรีม |
| `raw` | `0` \| `1` | `0` | `1` = เก็บค่า `-8190`/NAN ดิบด้วย |

**ตัวอย่าง**
```
GET /api/cr300/records?hours=6
GET /api/cr300/records?date=2026-11-09
GET /api/cr300/records?stream=live&hours=1
GET /api/cr300/records?hours=24&raw=1
```
**Response `200`**
```json
{
  "ok": true,
  "station": "narit_cr300",
  "stream": "averaged",
  "count": 6,
  "records": [
    {"TimeStamp": "2026-11-09 08:00:00", "RecNbr": "1700", "BattV_Min": "11.79", "WindDir_D1_WVT": "0.0"},
    {"TimeStamp": "2026-11-09 09:00:00", "RecNbr": "1701", "BattV_Min": "11.79", "WindDir_D1_WVT": "0.0"}
  ]
}
```
> **หมายเหตุ:** ถ้าข้อมูลเก่ากว่า `hours` ที่ขอ จะได้ `count: 0` — ใช้ `?date=` แทน

---

### `GET /days`
รายการวันที่มีข้อมูล

**Query:** `stream` (`averaged` \| `live`)
```
GET /api/cr300/days?stream=live
```
```json
{"stream": "averaged", "days": ["2026-10-29", "2026-10-30", "2026-11-09"]}
```

---

### `GET /fields`
รายชื่อคอลัมน์ทั้งหมดในสตรีมนั้น

**Query:** `stream`
```
GET /api/cr300/fields?stream=live
```
```json
{"stream": "live", "fields": ["BattV", "Invalid_Wind", "PTemp_C", "RecNbr", "TimeStamp"]}
```

---

### `GET /csv/{date}`
ดาวน์โหลด CSV ดิบของวันนั้น (เปิด Excel ได้)

**Path:** `date` = `YYYY-MM-DD` · **Query:** `stream`
```
GET /api/cr300/csv/2026-11-09
GET /api/cr300/csv/2026-11-09?stream=live
```
→ คืนไฟล์ `text/csv` · ไม่พบไฟล์ → `404 {"ok": false, "error": "not found"}`

---

## Data model — ความหมาย field

ค่าที่คืนขึ้นกับเซ็นเซอร์ที่ต่ออยู่จริง field ที่ไม่มีเซ็นเซอร์ (`-8190`/NAN) และ `PPFD`
(ขาลอย) ถูกกรองออกอัตโนมัติ — พอต่อเซ็นเซอร์จริงค่าจะโผล่มาเอง

| field | สตรีม | หน่วย | ความหมาย |
|-------|-------|-------|----------|
| `BattV` | live | V | แรงดันไฟเลี้ยง (ค่าปัจจุบัน) |
| `BattV_Min` | averaged | V | แรงดันไฟเลี้ยงต่ำสุดในชั่วโมง |
| `PTemp_C` | live | °C | อุณหภูมิแผงใน logger |
| `WindDir_D1_WVT` | averaged | ° | ทิศลมเฉลี่ย (ยังไม่ต่อเซ็นเซอร์ = 0) |
| `WindDir_SD1_WVT` | averaged | ° | ส่วนเบี่ยงเบนทิศลม |
| `Invalid_Wind` | live | — | นับ sample ลมที่ไม่ถูกต้อง |
| `RecNbr` | ทั้งคู่ | — | เลข record ของ logger |
| `TimeStamp` | ทั้งคู่ | — | เวลา (Asia/Bangkok) |

**field ที่จะโผล่เมื่อต่อเซ็นเซอร์จริง** (ตอนนี้เป็น -8190): `AirT_C`, `RH`, `WS_ms`,
`WindDir`, `Rain_mm`, `BP_mbar`, `SlrFD_W`, `Strikes`, `Dist_km`, `VWC`, `EC` ฯลฯ

---

## InfluxDB (ข้อมูลเดียวกัน)

`sensor-writer` ดึงจาก `/live` + `/latest` เขียนลง InfluxDB ให้ Grafana:

- **bucket:** `Lidar`
- **measurement:** `weather`
- **tags:** `station` (`narit_cr300`), `stream` (`live` / `averaged`)
- **timestamp:** เวลาจริงของ record (แปลงเป็น UTC)

Flux ตัวอย่าง (ค่าล่าสุดของ BattV เรียลไทม์):
```flux
from(bucket: "Lidar")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "weather" and r.stream == "live" and r._field == "BattV")
  |> last()
```

---

## สรุป endpoint

| Method | Path | ใช้ทำอะไร |
|--------|------|-----------|
| GET | `/api/cr300/health` | สถานะบริการ |
| GET | `/api/cr300/latest` | ค่าเฉลี่ยล่าสุด (รายชั่วโมง) |
| GET | `/api/cr300/live` | ค่าสดล่าสุด (เรียลไทม์) |
| GET | `/api/cr300/records` | หลาย record (`hours`/`date`/`stream`/`raw`) |
| GET | `/api/cr300/days` | วันที่มีข้อมูล |
| GET | `/api/cr300/fields` | รายชื่อคอลัมน์ |
| GET | `/api/cr300/csv/{date}` | ดาวน์โหลด CSV |
