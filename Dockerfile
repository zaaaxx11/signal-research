# syntax=docker/dockerfile:1
# Ztockz engine — one image, any host.
#
# Runtime state lives under /data (snapshot, cache, backups, SQLite). Mount it
# and the same image runs on a laptop, a VPS, or a scheduler. No host path is
# baked in: the app reads the MEE_* variables below or defaults to ./data.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEE_DATA_DIR=/data \
    MEE_ENGINE_CACHE_DIR=/data/cache \
    MEE_ENGINE_SNAPSHOT=/data/engine-snapshot.json \
    MEE_BACKUP_DIR=/data/backups \
    MEE_DB_PATH=/data/ztockz.db \
    MEE_EXPORT_DIR=/data/exports \
    MEE_SEC_RATE_LIMIT=10

WORKDIR /app

# Dependencies first so a source edit does not reinstall pyarrow.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . \
    && addgroup --system --gid 10001 ztockz \
    && adduser --system --uid 10001 --ingroup ztockz --home /app ztockz \
    && mkdir -p /data/cache /data/backups /data/exports \
    && chown -R ztockz:ztockz /app /data

USER ztockz

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/engine/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
