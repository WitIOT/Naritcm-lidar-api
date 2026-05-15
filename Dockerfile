FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt uvloop

COPY main_2.py /app/main_2.py
COPY rain_sensor.py /app/rain_sensor.py
COPY static /app/static

EXPOSE 8000
# --workers 1 เท่านั้น — GPIO (gpiochip0) เป็น hardware exclusive resource
# หลาย worker = หลาย process พยายาม open GPIO line เดิมพร้อมกัน → Errno 16 EBUSY
# concurrency ใช้ asyncio + background threads แทน (SensorCache, LimitCache ทำอยู่แล้ว)
CMD ["uvicorn", "main_2:app", "--host", "0.0.0.0", "--port", "8000", \
     "--loop", "uvloop", "--workers", "1"]
 