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
# --loop uvloop  → async I/O เร็วขึ้น ~20%
# --workers 2    → รับ request พร้อมกันได้มากขึ้น (ปรับตาม CPU core)
CMD ["uvicorn", "main_2:app", "--host", "0.0.0.0", "--port", "8000", \
     "--loop", "uvloop", "--workers", "2"]
 