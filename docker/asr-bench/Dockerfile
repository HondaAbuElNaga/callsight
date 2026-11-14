# GPU container for the speech-to-text benchmark (spec 2026-11-14-asr-benchmark).
# PyTorch built for CUDA 12.8: needed by RTX 50-series (Blackwell, sm_120) GPUs.
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/models HF_HUB_DISABLE_TELEMETRY=1
ARG REQS=requirements.txt
COPY docker/asr-bench/${REQS} ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY callsight/__init__.py ./callsight/__init__.py
COPY callsight/asr ./callsight/asr
RUN useradd -m -u 1000 app && mkdir -p /models /out && chown app /models /out
USER app
ENTRYPOINT ["python", "-m", "callsight.asr.bench"]
