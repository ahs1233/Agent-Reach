FROM node:24-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AHMED_TOOLBOX_HOST=0.0.0.0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-venv python3-pip git curl ca-certificates gh ffmpeg chromium tesseract-ocr tesseract-ocr-ara tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY agent_reach ./agent_reach
COPY config ./config

RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir -U yt-dlp-getpot-wpc parth-dl faster-whisper \
    && npm install -g mcporter@0.13.13

RUN npm install --prefix /app youtubei.js@latest

# Bake the local Whisper model into the image so startup never depends on HF Hub.
ARG AHMED_LOCAL_WHISPER_MODEL=small
RUN AHMED_MODEL="$AHMED_LOCAL_WHISPER_MODEL" python -c 'import os; from faster_whisper import WhisperModel; WhisperModel(os.environ["AHMED_MODEL"], device="cpu", compute_type="int8")'

# Install the matching bgutil PO-token runtime in the same container so
# yt-dlp can reach it over loopback without provisioning a Railway sidecar.
ARG BGUTIL_POT_VERSION=2.0.0
RUN git clone --depth 1 --branch "${BGUTIL_POT_VERSION}" https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil-pot \
    && cd /opt/bgutil-pot/server \
    && npm ci \
    && npx tsc \
    && npm prune --omit=dev

COPY docker/start-ahmed-toolbox.sh /usr/local/bin/start-ahmed-toolbox
RUN chmod +x /usr/local/bin/start-ahmed-toolbox

EXPOSE 8765

CMD ["/usr/local/bin/start-ahmed-toolbox"]
