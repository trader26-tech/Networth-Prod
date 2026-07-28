# Multi-stage Dockerfile — build Angular frontend, then run FastAPI that
# serves both the API and the SPA. Useful as a fallback if Railway's nixpacks
# build ever has trouble; deploy with `docker build` + `docker run` or push
# to Railway's image-deploy mode.

# ── Stage 1: build Angular ───────────────────────────────────────────────────
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx ng build --configuration=production

# ── Stage 2: Python runtime ──────────────────────────────────────────────────
FROM python:3.11-slim
WORKDIR /app

# System deps that some Python wheels need (kiteconnect / pandas etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# --retries/--timeout make the install resilient to transient PyPI/network blips
# during the build (e.g. a platform incident), which otherwise fail the deploy.
# --prefer-binary avoids accidental from-source compiles when a wheel exists.
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefer-binary --retries 5 --timeout 120 -r requirements.txt

# Copy backend code + the Angular dist from stage 1
COPY api/ ./api/
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Bring in the start shim that reads PORT from os.environ directly — avoids
# any platform-specific shell-expansion quirks (Railway exec's commands
# without going through sh, so `${PORT:-8000}` would otherwise be passed
# literally to uvicorn).
COPY start.py ./

CMD ["python", "start.py"]
