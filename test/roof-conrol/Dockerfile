FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main2.py .

# ใช้ host network และเข้าถึง /dev/gpiochip*
CMD ["uvicorn", "main2:app", "--host", "0.0.0.0", "--port", "8085"]