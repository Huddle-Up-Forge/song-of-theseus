# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM alpine:3.21 AS plus-source
ARG PLUS_SOURCE_REVISION=baa409c2e7e5e775f09b4e92a220808f4827d2cc
RUN apk add --no-cache git \
    && git init /upstream \
    && git -C /upstream remote add origin https://github.com/ASLP-lab/YingMusic-Singer-Plus.git \
    && git -C /upstream fetch --depth=1 origin "${PLUS_SOURCE_REVISION}" \
    && git -C /upstream checkout --detach FETCH_HEAD

FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="Song of Theseus" \
      org.opencontainers.image.description="Local singing and lyric editing workstation" \
      org.opencontainers.image.source="https://github.com/Huddle-Up-Forge/song-of-theseus" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    YINGSTUDIO_HOST=0.0.0.0 \
    YINGSTUDIO_PORT=23253 \
    YINGSTUDIO_DATA_ROOT=/data \
    YINGSTUDIO_MODEL_ROOT=/data/models \
    YINGMUSIC_PLUS_ROOT=/opt/YingMusic-Singer-Plus \
    HF_HOME=/data/models/huggingface \
    HF_HUB_CACHE=/data/models/huggingface/hub \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TORCH_HOME=/data/models/torch \
    PYTHONPATH=/app:/opt/YingMusic-Singer-Plus

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential \
        curl \
        espeak-ng \
        ffmpeg \
        libespeak-ng1 \
        libsndfile1 \
        python3.12 \
        python3.12-dev \
        python3.12-venv \
    && python3.12 -m venv /opt/venv \
    && rm -rf /var/lib/apt/lists/*

ENV PATH=/opt/venv/bin:${PATH}

WORKDIR /app
COPY requirements.txt requirements-docker.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir \
        torch==2.9.1 torchvision==0.24.1 torchaudio==2.9.1 \
        --index-url https://download.pytorch.org/whl/cu128 \
    && python -m pip install --no-cache-dir -r requirements-docker.txt

COPY . /app
COPY --from=frontend-build /build/dist /app/frontend/dist
COPY --from=plus-source /upstream/src /opt/YingMusic-Singer-Plus/src
COPY --from=plus-source /upstream/LICENSE /opt/YingMusic-Singer-Plus/LICENSE
COPY --from=plus-source /upstream/LICENSE-STABILITY /opt/YingMusic-Singer-Plus/LICENSE-STABILITY
COPY --from=plus-source /upstream/README.md /opt/YingMusic-Singer-Plus/README.md

RUN mkdir -p /data/models /data/sessions /data/singers \
    && python -c "import torch, torchaudio; print('PyTorch', torch.__version__, 'CUDA', torch.version.cuda, 'torchaudio', torchaudio.__version__)" \
    && python -c "from src.YingMusicSinger.infer.YingMusicSinger import YingMusicSinger; print('YingMusic-Singer-Plus inference imports ready')"

VOLUME ["/data"]
EXPOSE 23253
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:23253/api/health || exit 1

CMD ["python", "app.py"]
