# Multi-stage build: React front end -> Python API that also serves the built site on one port.

# ---- stage 1: build the React app (base './' so it works at "/" behind the API) ----
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- stage 2: the API ----
FROM python:3.12-slim AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements-api.txt ./
RUN pip install -r requirements-api.txt
COPY app ./app
COPY db ./db
COPY policies ./policies
COPY --from=web /web/dist ./frontend/dist
# Run as an unprivileged user; the audit log lives in a writable volume.
RUN useradd --system --create-home t2s && mkdir -p /data/audit && chown -R t2s /data
USER t2s
ENV T2S_AUDIT_PATH=/data/audit/audit.jsonl
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
