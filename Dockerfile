FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-optional-media.txt ./
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.1.2" \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && (python -m pip install --no-cache-dir -r requirements-optional-media.txt || true) \
    && python -m pip check

COPY . .
RUN python -m compileall -q app tools

CMD ["sh", "-c", "uvicorn app.webhook_server:api --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
