# --- web page (React) build: Node only exists in this stage ---
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY web/ ./
RUN npm run build

# --- app ---
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WEB_DIST=/app/web/dist
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini pyproject.toml ./
COPY callsight ./callsight
COPY config ./config
COPY --from=web /web/dist ./web/dist
RUN useradd -m -u 1000 app && mkdir -p /data/audio && chown app /data/audio
USER app
