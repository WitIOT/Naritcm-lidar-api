FROM python:3.9-slim

WORKDIR /app

RUN pip install --no-cache-dir \
  requests==2.32.3 \
  influxdb-client==1.47.0

COPY writer.py /app/writer.py

CMD ["python", "/app/writer.py"]