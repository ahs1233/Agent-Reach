FROM node:24-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AHMED_TOOLBOX_HOST=0.0.0.0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-venv python3-pip git curl ca-certificates gh \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY agent_reach ./agent_reach
COPY config ./config

RUN pip install --no-cache-dir . \
    && npm install -g mcporter@0.13.13

EXPOSE 8765

CMD ["ahmed-toolbox"]
