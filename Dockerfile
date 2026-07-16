# syntax=docker/dockerfile:1
#
# Multi-stage build:
#   1. "builder" installs dependencies into /install
#   2. final stage copies ONLY /install + app code — no pip cache, no
#      package index metadata, no build tools in the shipped image
#
# Base is `python:3.12-slim` (Debian), not `-alpine`. Every dependency in
# requirements.txt (fastapi, pydantic, sqlalchemy, aiosqlite, httpx,
# pyjwt, redis, ...) ships prebuilt manylinux wheels, so pip never
# compiles anything here — this is what actually makes the build fast.
# Alpine would look smaller on paper but its musl libc has no matching
# wheels for several of these packages, so pip falls back to compiling
# from source (needs gcc/musl-dev, and is genuinely slow — the exact
# "takes lots of time" problem you're trying to avoid). Slim is the
# faster AND simpler choice here, not a size/speed tradeoff.
#
# REQUIRED BEFORE BUILDING: put your real legacy/lotus_monitor.py and
# legacy/strategy_engine.py in place — see legacy/README.md. The app
# will fail at container startup (not at build time) without them.

FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim

# Real Docker packaging convention. PYTHONDONTWRITEBYTECODE: don't waste
# time/space writing .pyc files in an ephemeral container.
# PYTHONUNBUFFERED: flush stdout immediately, so `docker logs` shows
# output as it happens instead of buffered in chunks.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder /install /usr/local

COPY app/ ./app
COPY legacy/ ./legacy

# Not copied: .env (never bake secrets/config into an image layer — pass
# at `docker run --env-file .env` or via your orchestrator instead),
# static/ and index1.html (optional frontend — main.py already handles
# their absence gracefully; add a `COPY static/ ./static` +
# `COPY index1.html .` line here if you have them and want them baked in).

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p uploads/avatars data/uploads/avatars \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# Lightweight healthcheck using Python's stdlib (urllib) instead of curl —
# avoids an extra `apt-get install curl` layer just for this.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:5000/api/health', timeout=2).status == 200 else 1)"]

# Single worker deliberately — this app runs background polling tasks
# (market fetch, crypto/commodities polling) in-process via asyncio.Task.
# Running --workers > 1 would multiply those loops across processes,
# hammering upstream feeds N times and writing to SQLite from N
# processes concurrently. See README_MIGRATION.md's Deployment section
# before changing this.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000"]