FROM python:3.9-slim

WORKDIR /app

# (ตัวเลือก) เครื่องมือพื้นฐาน + CA certs
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# ติดตั้งไลบรารีที่จำเป็น
# uvicorn[standard] จะดึง uvloop/httptools ฯลฯ (มี wheel สำหรับ manylinux ส่วนใหญ่)
RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    "uvicorn[standard]==0.34.0" \
    pymodbus==3.8.6 \
    python-periphery==2.4.1

# คัดลอกโค้ด + static
COPY main.py /app/main.py
COPY static /app/static

EXPOSE 8000

# รันเป็น root เพื่อเข้าถึง /dev/gpiochip0 ได้ง่ายสุด (ในงาน IO หน้างาน)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
