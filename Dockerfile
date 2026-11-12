FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini pyproject.toml ./
COPY callsight ./callsight
COPY config ./config
RUN useradd -m -u 1000 app && mkdir -p /data/audio && chown app /data/audio
USER app
