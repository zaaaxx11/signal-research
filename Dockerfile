# syntax=docker/dockerfile:1
# Ztockz engine — one image, any host.
#
# Runtime state lives under /data (snapshot, cache, backups, SQLite). Mount it
# and the same image runs on a laptop, a VPS, or a scheduler. No host path is
# baked in: the app reads the MEE_* variables below or defaults to ./data.
#
# Two stages, because the product is two builds. The shell in ``web/`` is a Vite
# app that compiles to ``web/dist``; ``dist`` is gitignored and the Python
# package does not carry it (``pyproject.toml`` packages ``app*`` only), so the
# image is the only artefact that can ship both halves. Before this stage
# existed the image copied neither, and every ``/app`` URL answered 404 while
# the container reported healthy.
#
# The web stage runs the same gate a developer runs — ``npm run check`` is
# vitest, ``tsc --noEmit``, ``vite build`` and the 180 KB budget — so a broken
# or oversized shell fails ``docker build`` instead of the first request.
FROM node:22-bookworm-slim AS web

WORKDIR /src

# The lockfile alone first: a source edit must not reinstall the dependency
# tree. ``npm ci`` refuses a lockfile that disagrees with ``package.json``,
# which is the behaviour a reproducible image wants.
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web ./

# ``check`` ends in ``vite build``; the test makes a renamed script a build
# failure instead of an empty COPY below.
RUN npm run check \
    && test -f dist/index.html \
    && test -s dist/index.html

# ---------------------------------------------------------------------------
FROM python:3.14-slim

# ``MEE_SERVE_UI=1``: this image ships the shell, so the process promises one.
# A missing ``web/dist`` is then a broken image, and /health answers 503 instead
# of calling it healthy (app/api/health.py ``resolve_web_ui``).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEE_DATA_DIR=/data \
    MEE_ENGINE_CACHE_DIR=/data/cache \
    MEE_ENGINE_SNAPSHOT=/data/engine-snapshot.json \
    MEE_BACKUP_DIR=/data/backups \
    MEE_DB_PATH=/data/ztockz.db \
    MEE_EXPORT_DIR=/data/exports \
    MEE_SEC_RATE_LIMIT=10 \
    MEE_SERVE_UI=1

WORKDIR /app

# Dependencies first so a source edit does not reinstall pyarrow.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . \
    && addgroup --system --gid 10001 ztockz \
    && adduser --system --uid 10001 --ingroup ztockz --home /app ztockz \
    && mkdir -p /data/cache /data/backups /data/exports \
    && chown -R ztockz:ztockz /app /data

# The shell, from the stage that built and gated it. Nothing else crosses: the
# Node toolchain and ``node_modules`` stay in the build stage.
COPY --from=web --chown=ztockz:ztockz /src/dist ./web/dist

# A build that lost the artefact must fail here, where it costs seconds, not at
# the first request from a user.
RUN test -f /app/web/dist/index.html \
    && test -d /app/web/dist/assets

USER ztockz

EXPOSE 8080

# The gate is the two things this image promises: the API answers, and the
# shell it ships is mounted. It is **not** ``/health``'s overall verdict — a
# snapshot-less container is a documented state ("the engine starts empty on
# purpose") that ``/health`` reports as ``failing`` because a configured
# snapshot that is missing is untrusted data. That verdict belongs to the
# operator's monitor; the container's liveness keeps the meaning it had, plus
# the shell. Before this, the healthcheck watched only the engine and a
# container whose ``/app`` 404'd still reported healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import http.client,json,sys; c=http.client.HTTPConnection('127.0.0.1',8080,timeout=3); c.request('GET','/health'); r=c.getresponse(); h=json.loads(r.read()); c=http.client.HTTPConnection('127.0.0.1',8080,timeout=3); c.request('GET','/app'); s=c.getresponse(); body=s.read(); sys.exit(0 if h['ui']['ok'] and s.status==200 and 'text/html' in (s.getheader('content-type') or '') and b'<div id=\"root\">' in body else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
