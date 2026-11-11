#!/usr/bin/env python3
"""
cr300_test.py — ทดสอบการเชื่อมต่อ/ดึงข้อมูล Campbell CR300 ผ่าน serial (PakBus)
แยกจาก collector เพื่อไล่หาปัญหาพอร์ต/สาย/baud ได้เร็ว (timeout สั้น รายงานทุกสเต็ป)

ใช้ไลบรารีเดียวกับ collector: pycampbellcr1000 + pyserial

ตัวอย่างใช้งาน
--------------
# ในคอนเทนเนอร์ (มีไลบรารีอยู่แล้ว) — พอร์ตถูก map เป็น /dev/cr300:
  docker compose run --rm cr300-collector python -u /test/cr300_test.py
  # (ต้อง mount สคริปต์เข้าไปก่อน ดูหมายเหตุท้ายไฟล์ หรือรันบน host ที่ pip ติดตั้งแล้ว)

# บน host โดยตรง (ติดตั้ง: pip install pycampbellcr1000 pyserial):
  python3 cr300_test.py --url serial:/dev/ttyRS232:9600
  python3 cr300_test.py --url serial:/dev/ttyRS232:9600 --timeout 5
  python3 cr300_test.py --url serial:/dev/ttyRS232:9600 --loop 60      # poll Public ทุก 60 วิ
  python3 cr300_test.py --raw --url serial:/dev/ttyRS232:9600          # ดู byte ดิบที่วิ่งกลับมา
"""
import argparse
import sys
import time
from datetime import datetime


def log(msg):
    print(f"{datetime.now():%H:%M:%S}  {msg}", flush=True)


def parse_url(url):
    # serial:/dev/ttyRS232:9600  ->  ('/dev/ttyRS232', 9600)
    try:
        _, dev, baud = url.split(":")
        return dev, int(baud)
    except ValueError:
        raise SystemExit(f"URL ผิดรูปแบบ: {url!r}  (ต้องเป็น serial:/dev/xxx:baud)")


def raw_probe(url, seconds):
    """โหมดดิบ: เปิด pyserial ยิง PakBus ring แล้วดูว่ามี byte อะไรวิ่งกลับมาไหม
    ใช้เช็กระดับสัญญาณ/สายแบบไม่พึ่ง library (มี byte กลับ = เส้นทางพอใช้ได้)"""
    import serial
    dev, baud = parse_url(url)
    log(f"[RAW] เปิด {dev} @ {baud} (timeout 1s)")
    with serial.Serial(dev, baud, timeout=1) as s:
        # PakBus "ring" แบบง่าย (SerSyncByte 0xBD ล้อมเฟรม) — ปลุก/กระตุ้นให้ logger ตอบ
        ring = bytes([0xBD, 0xBD, 0xBD, 0xBD])
        got = bytearray()
        t0 = time.time()
        while time.time() - t0 < seconds:
            s.write(ring)
            time.sleep(0.2)
            chunk = s.read(256)
            if chunk:
                got += chunk
                log(f"[RAW] ได้ {len(chunk)} byte: {chunk.hex(' ')}")
        if got:
            log(f"[RAW] รวม {len(got)} byte — มีสัญญาณกลับ เส้นทางพอใช้ได้ (ปัญหาอาจอยู่ที่ framing/baud)")
        else:
            log("[RAW] ไม่มี byte กลับเลยใน %ds — สาย/พอร์ต/ระดับแรงดันน่าจะมีปัญหา" % seconds)
    return 0 if got else 2


def main():
    ap = argparse.ArgumentParser(description="ทดสอบ CR300 ผ่าน serial")
    ap.add_argument("--url", default="serial:/dev/cr300:9600",
                    help="serial:/dev/xxx:baud (default serial:/dev/cr300:9600)")
    ap.add_argument("--timeout", type=int, default=5, help="serial timeout วินาที (default 5)")
    ap.add_argument("--table", default="Public", help="table ที่จะอ่าน (default Public)")
    ap.add_argument("--retries", type=int, default=2, help="จำนวนครั้ง retry ตอนต่อ (default 2)")
    ap.add_argument("--loop", type=int, default=0, metavar="SEC",
                    help="poll ซ้ำทุก SEC วินาที (0 = รันครั้งเดียว)")
    ap.add_argument("--raw", action="store_true", help="โหมดดิบ: ดู byte ที่วิ่งกลับมา (ไม่ใช้ PakBus lib)")
    args = ap.parse_args()

    if args.raw:
        sys.exit(raw_probe(args.url, max(args.timeout, 5)))

    try:
        from pycampbellcr1000 import CR1000
    except ImportError:
        raise SystemExit("ไม่เจอ pycampbellcr1000 — ติดตั้ง: pip install pycampbellcr1000 pyserial")

    dev, baud = parse_url(args.url)
    log(f"URL={args.url}  dev={dev}  baud={baud}  timeout={args.timeout}s")

    # ---- STEP 1: เปิดการเชื่อมต่อ ----
    device = None
    for attempt in range(1, args.retries + 2):
        try:
            log(f"[1] เปิดการเชื่อมต่อ (ครั้งที่ {attempt}) ...")
            device = CR1000.from_url(args.url, timeout=args.timeout)
            log("[1] ✓ เปิดพอร์ตได้")
            break
        except Exception as e:
            log(f"[1] ✗ {type(e).__name__}: {e}")
            if attempt <= args.retries:
                time.sleep(1)
            else:
                log("    เปิดพอร์ตไม่สำเร็จ — เช็ก device path / สิทธิ์ / พอร์ตถูกตัวอื่นยึด")
                sys.exit(2)

    # ---- STEP 2: ping + อ่านนาฬิกา (พิสูจน์ว่า PakBus คุยกันได้จริง) ----
    try:
        log("[2] ping_node ...")
        device.ping_node()
        clk = device.gettime()
        log(f"[2] ✓ CR300 ตอบ! นาฬิกา logger = {clk}  (local = logger - 7h)")
    except Exception as e:
        log(f"[2] ✗ {type(e).__name__}: {e}")
        log("    เปิดพอร์ตได้แต่ CR300 ไม่ตอบ PakBus = สาย/ระดับสัญญาณ/baud ไม่ตรง")
        sys.exit(3)

    # ---- STEP 3: list tables ----
    try:
        log("[3] list_tables ...")
        tables = device.list_tables()
        log(f"[3] ✓ tables: {', '.join(str(t) for t in tables)}")
    except Exception as e:
        log(f"[3] ✗ {type(e).__name__}: {e}")
        tables = []

    # ---- STEP 4: อ่าน Public (collect-all) ----
    def read_once():
        try:
            log(f"[4] อ่าน table {args.table!r} (collect-all) ...")
            data, _ = device._collect_data(args.table)
            if not data:
                log(f"[4] △ {args.table} ว่าง (ไม่มี record)")
                return
            row = data[-1]  # แถวล่าสุด
            log(f"[4] ✓ ได้ {len(data)} record — แถวล่าสุด:")
            for k, v in row.items():
                if isinstance(v, bytes):
                    continue  # ข้าม field binary (เช่น CVMeta)
                log(f"        {k:16} = {v}")
        except Exception as e:
            log(f"[4] ✗ {type(e).__name__}: {e}")

    read_once()

    if args.loop > 0:
        log(f"--- เข้าโหมด loop: poll ทุก {args.loop} วิ (Ctrl+C ออก) ---")
        try:
            while True:
                time.sleep(args.loop)
                read_once()
        except KeyboardInterrupt:
            log("ออกจาก loop")

    log("เสร็จ — ถ้าเห็น BattV/PTemp_C แปลว่าเส้นทาง RS-232 ใช้งานได้แล้ว ✓")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# วิธีรันในคอนเทนเนอร์ (ไลบรารีพร้อม ไม่ต้อง pip):
#   # จาก ~/lidar-api — mount สคริปต์เข้าไปแล้วรัน (ข้าม 15m ได้ถ้า map พอร์ตสั้น)
#   docker compose run --rm -v "$PWD/cr300_test.py:/test.py" \
#       cr300-collector python -u /test.py --url serial:/dev/cr300:9600
#
# วิธีรันบน host โดยตรง (ติดตั้งก่อน):
#   pip install --break-system-packages pycampbellcr1000 pyserial
#   python3 cr300_test.py --url serial:/dev/ttyRS232:9600
# ---------------------------------------------------------------------------
