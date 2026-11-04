FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# tzdata so local-time handling is correct
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# pycampbellcr1000 = PakBus; influxdb-client = write to InfluxDB
RUN pip install --no-cache-dir pycampbellcr1000 pyserial influxdb-client

WORKDIR /app
COPY collector.py ./

CMD ["python", "collector.py"]
