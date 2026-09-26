FROM node:24-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AHMED_TOOLBOX_HOST=0.0.0.0 \
    BU_CDP_URL=http://127.0.0.1:9222 \
    BH_CHROME_PATH=/usr/bin/chromium \
    CHROME_PATH=/usr/bin/chromium

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-venv python3-pip git curl ca-certificates gh chromium \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY agent_reach ./agent_reach
COPY config ./config
COPY scripts ./scripts
COPY scripts/start-ahmed-toolbox.sh /usr/local/bin/start-ahmed-toolbox

RUN pip install --no-cache-dir ".[interactive-browser]" \
    && command -v browser-use \
    && command -v chromium \
    && test -f /app/scripts/dual-temporal-live-acceptance.py \
    && chmod +x /usr/local/bin/start-ahmed-toolbox \
    && npm install -g mcporter@0.13.13

EXPOSE 8765

CMD ["start-ahmed-toolbox"]
