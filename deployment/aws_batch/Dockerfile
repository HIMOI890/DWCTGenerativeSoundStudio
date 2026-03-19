FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install backend
COPY studio/edmg-studio/python_backend /app/python_backend
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -e /app/python_backend \
    && pip install --no-cache-dir boto3 requests

# Batch worker
COPY deployment/aws_batch/worker.py /app/worker.py

ENTRYPOINT ["python", "/app/worker.py"]
