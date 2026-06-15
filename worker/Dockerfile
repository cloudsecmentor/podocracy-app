FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY local_worker.py worker_poll.py ./
COPY processing_container ./processing_container
COPY common ./common
COPY frontend ./frontend

RUN chmod +x /app/processing_container/pd-051-ffmpeg-norm.sh

CMD ["python", "worker_poll.py"]
