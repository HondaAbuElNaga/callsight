# GPU Celery worker for queue `asr` (spec 2026-11-14-transcription). Same base + exact-pinned model libraries as
# docker/asr-bench (layers shared with that image), plus the app requirements and code.
# Jais (transformers 5) image: build args REQS=requirements-jais.txt TORCH_REQS=requirements-jais-torch.txt.
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_OFFLINE=1
ARG REQS=requirements.txt
ARG TORCH_REQS=
COPY docker/asr-bench/*.txt ./reqs/
RUN if [ -n "$TORCH_REQS" ]; then       pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cu128 -r reqs/$TORCH_REQS; fi  && pip install --no-cache-dir -r reqs/$REQS
COPY requirements.txt ./reqs/app.txt
COPY requirements.in ./reqs/requirements.in
RUN pip install --no-cache-dir -r reqs/app.txt
# speaker split (spec 2026-11-17-diarization): pyannote 3.3.2, exact pins, keeps torch / numpy of the base image
COPY docker/asr-worker/requirements-diarize.txt ./reqs/diarize.txt
RUN pip install --no-cache-dir -r reqs/diarize.txt
COPY alembic.ini pyproject.toml ./
COPY callsight ./callsight
COPY config ./config
RUN useradd -m -u 1000 app && mkdir -p /models /data/audio && chown app /models
USER app
CMD ["celery", "-A", "callsight.asr.tasks", "worker", "-Q", "asr", "--concurrency=1", "--prefetch-multiplier=1", "-n", "asr@%h", "-l", "info"]
