# CR300 weather pipeline (ส่วนของ Naritcm-lidar-api)

ดึงข้อมูล Campbell Scientific **CR300** ผ่าน **RS-232** → CSV → REST API (`/api/cr300/*`
บน port 8000 เดียวกับ lidar-api) → **InfluxDB** (measurement `weather`) ผ่าน `sensor-writer`

```
CR300 ──RS232──> /dev/cr300 ──> cr300-collector ──> CSV (volume cr300_data)
                                                      │
                            naritcm-lidar-api :8000  /api/cr300/*  <──┘
                                                      │
                            sensor-writer ──> InfluxDB (bucket Lidar, measurement weather)
```

- **cr300-collector** — อ่าน PakBus, เขียน CSV + latest.json ลง volume `cr300_data` (ไม่เขียน InfluxDB เอง)
- **naritcm-lidar-api** — mount volume อ่าน CSV มาเสิร์ฟ `/api/cr300/*`
- **sensor-writer** — poll `/api/cr300/live` + `/latest` เขียน InfluxDB (เหมือน room1/rain)

---

## 1. ตั้งค่า serial port ให้คงที่ — `/dev/cr300` (udev symlink)

เลข `ttyACMx` / `ttyUSBx` **ไม่คงที่** พอ reboot/ถอดเสียบจะเลื่อน ต้องผูกเป็นชื่อคงที่ `/dev/cr300`

### หา vendor/model ของ adapter ที่ใช้
```bash
# เสียบ USB-RS232 adapter แล้วดูว่าขึ้นเป็น device อะไร
dmesg | grep -iE "tty(USB|ACM)" | tail
ls -l /dev/ttyUSB* /dev/ttyACM*

# ดู property ของ device นั้น (เปลี่ยน ttyUSB0 เป็นตัวที่เจอ)
udevadm info -q property -n /dev/ttyUSB0 | grep -E "ID_VENDOR_ID|ID_MODEL_ID|ID_USB_INTERFACE_NUM|ID_SERIAL_SHORT"
```

### เขียน udev rule
สร้าง `/etc/udev/rules.d/99-cr300.rules` (ดูไฟล์ตัวอย่าง `99-cr300.rules` ในโฟลเดอร์นี้):

```bash
# ตัวอย่าง — IRIV PiControl ใช้ชิป CH342 interface 02 (1a86:55d2)
SUBSYSTEM=="tty", ENV{ID_VENDOR_ID}=="1a86", ENV{ID_MODEL_ID}=="55d2", ENV{ID_USB_INTERFACE_NUM}=="02", SYMLINK+="cr300"
```

> **เครื่องใหม่ (เช่น raspberrypi) ที่ใช้ USB-RS232 adapter คนละรุ่น** — แก้ `ID_VENDOR_ID`/`ID_MODEL_ID`
> ให้ตรงกับที่ได้จาก `udevadm info`. ตัวอย่างชิปที่พบบ่อย:
> - FTDI FT232: `1a86` ไม่ใช่ — FTDI = `0403`, model เช่น `6001`
> - CH340: `1a86:7523`  ·  CP210x: `10c4:ea60`  ·  PL2303: `067b:2303`
>
> **⚠️ ระวังชนกับ RG-15** — rain sensor บนเครื่อง lidar ใช้ CH342G (`1a86`) เหมือนกัน ถ้า CR300 adapter
> เป็น CH34x ด้วย ให้แยกด้วย **serial number** เพิ่ม `ENV{ID_SERIAL_SHORT}=="<ของ CR300>"` ใน rule
> หรือใช้ `ENV{ID_USB_INTERFACE_NUM}` ให้ต่างกัน

โหลด rule + สร้าง symlink:
```bash
sudo cp cr300/99-cr300.rules /etc/udev/rules.d/99-cr300.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=tty
ls -l /dev/cr300        # ต้องชี้ไป ttyUSBx/ttyACMx
```

`docker-compose.yml` map `"/dev/cr300:/dev/cr300"` ให้ container — ต้องมี `/dev/cr300` บน host ก่อน

---

## 2. ปิด ModemManager (ถ้ามี)

ModemManager จะ probe ttyACM/ttyUSB ตอน boot แล้วยึดพอร์ต (`Device or resource busy`) — Pi ที่ไม่ได้ใช้
cellular modem ควรปิด:
```bash
sudo systemctl disable --now ModemManager
```
(ไฟล์ udev ด้านบนมีบรรทัด `ID_MM_DEVICE_IGNORE=1` กันไว้อีกชั้นเผื่อ ModemManager ถูกเปิดกลับมา)

---

## 3. การเดินสาย (RS-232)

CAT6/สายสัญญาณ → DB9 ที่ฝั่ง CR300 (ไขว้ TX/RX):

| CR300 DB9 | สัญญาณ | ปลาย IRIV/adapter |
|-----------|--------|-------------------|
| pin 2 | TX (out) | Rx |
| pin 3 | RX (in) | Tx |
| pin 5 | GND | GND |

- ต่อ shield ลงกราวด์ **ปลายเดียว** (ฝั่ง adapter/เครื่อง)
- ถ้าไม่มีข้อมูล ลองสลับ pin 2 ↔ 3
- **สาย solid core หักง่ายที่จุดต่อ** → ใช้สายอ่อน (LiYCY) + DB9 screw-terminal + ferrule + strain relief

---

## 4. ค่า config ของ collector (env ใน docker-compose.yml)

| env | ค่า | ความหมาย |
|-----|-----|----------|
| `CR300_URL` | `serial:/dev/cr300:115200` | พอร์ต + baud (RS-232 default 115200) |
| `CR300_TABLE` | `Every60` | table averaged (รายชั่วโมง) |
| `PUBLIC_TABLE` | `Public` | table live (ค่าปัจจุบัน) |
| `PUBLIC_POLL_SECONDS` | `60` | ดึง live ทุกกี่วินาที (0 = ปิด live) |
| `POLL_SECONDS` | `300` | เช็ก table averaged ทุกกี่วินาที |
| `BACKFILL_HOURS` | `6` | ครั้งแรกดึงย้อนหลังกี่ชั่วโมง |
| `LOGGER_OFFSET_HOURS` | `-7` | นาฬิกา CR300 เร็วไป 7 ชม. (ตั้ง 0 ถ้าแก้นาฬิกาแล้ว) |
| `STATION` | `narit_cr300` | tag station |
| `DATA_HOME` | `/data/cr300` | ที่เก็บ CSV (volume แชร์) |
| `IGNORE_FIELDS` | `PPFD,PPFD_Avg` | field ที่ลบทิ้ง (PPFD = ขาลอย ไม่มีเซ็นเซอร์จริง) |

> **collector ไม่ตั้ง `INFLUX_*`** โดยตั้งใจ → เขียน CSV อย่างเดียว ให้ `sensor-writer` เป็นคนเขียน InfluxDB

### PakBus / logger
- **baud** 115200, **PakBus address** 1, **security code** 0
- RS-232 ของ CR300 ไม่ autobaud — ต้องตรงกันสองฝั่ง (ตั้งที่ Device Configuration Utility)
- **อย่า "Send Program"** ทับ ถ้าไม่จำเป็น — จะลบ data table เดิมทั้งหมด

---

## 5. ค่า config ของ writer (เขียน InfluxDB)

| env | ค่า | ความหมาย |
|-----|-----|----------|
| `CR300_LIVE_URL` | `http://naritcm-lidar-api:8000/api/cr300/live` | ดึง live |
| `CR300_LATEST_URL` | `.../api/cr300/latest` | ดึง averaged |
| `WEATHER_MEASUREMENT` | `weather` | measurement ใน InfluxDB |
| `WEATHER_TZ_OFFSET_HOURS` | `7` | เวลา CR300 เป็น Asia/Bangkok → แปลงเป็น UTC |
| `INFLUX_TOKEN` | `${INFLUX_TOKEN}` | จาก `.env` (ไม่ commit) |

เขียนลง measurement `weather`, tag `station` + `stream` (`live`/`averaged`), dedup ด้วย timestamp

---

## 6. Deploy

```bash
# 0. ต้องมี /dev/cr300 (ข้อ 1) + .env (INFLUX_TOKEN)
cp .env.example .env && nano .env     # ใส่ token (rotate ตัวใหม่)

# 1. build + run ทั้ง stack
docker compose up -d --build
docker compose ps
docker compose logs --tail=20 cr300-collector

# 2. ทดสอบ (port 8000 เดียว)
curl -s localhost:8000/api/cr300/live
curl -s localhost:8000/api/cr300/latest
curl -s "localhost:8000/api/cr300/records?hours=6"
curl -s localhost:8000/api/sensor      # lidar เดิม ยังทำงาน

# 3. เช็ก InfluxDB measurement weather มีข้อมูล → Grafana
```

---

## 7. Troubleshooting (สรุปจากที่เคยเจอ)

| อาการ | สาเหตุ / แก้ |
|-------|-------------|
| `could not open /dev/cr300` หลัง reboot | พอร์ตเลื่อน → ตั้ง udev (ข้อ 1) |
| `Device or resource busy` ตอน boot | ModemManager แย่งพอร์ต → ปิด (ข้อ 2) |
| `NoDeviceException` ทั้งที่พอร์ตถูก | สาย RS-232 หลวม/ขาด (solid core หักง่าย) → เช็ก/เปลี่ยนสายอ่อน |
| `unpack requires a buffer of 8 bytes` | ปกติ — พอร์ต CR300 ตื่นจากหลับ, retry เอาเอง |
| ข้อมูลขาดช่วงไฟดับ | CR300 เก็บในตัว → พอกลับมา collector backfill ให้เอง |
| `/api/cr300/records?hours=N` ได้ 0 | ข้อมูลเก่ากว่า N ชม. → ใช้ `?date=YYYY-MM-DD` |
| `/api/cr300/live` ว่าง (503) | ยังไม่มี snapshot / collector ยังต่อ CR300 ไม่ได้ |
