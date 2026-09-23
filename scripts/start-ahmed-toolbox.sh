#!/bin/sh
set -eu

PROFILE_DIR="${AHMED_BROWSER_PROFILE_DIR:-/tmp/ahmed-toolbox-chromium}"
CDP_URL="${BU_CDP_URL:-http://127.0.0.1:9222}"

mkdir -p "$PROFILE_DIR"

chromium \
  --headless=new \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE_DIR" \
  about:blank \
  >/tmp/ahmed-chromium.log 2>&1 &
CHROMIUM_PID=$!

attempt=0
until curl -fsS "$CDP_URL/json/version" >/dev/null 2>&1; do
  if ! kill -0 "$CHROMIUM_PID" 2>/dev/null; then
    echo "Chromium exited before CDP became ready" >&2
    cat /tmp/ahmed-chromium.log >&2 || true
    exit 1
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "Timed out waiting for Chromium CDP at $CDP_URL" >&2
    cat /tmp/ahmed-chromium.log >&2 || true
    exit 1
  fi
  sleep 1
done

echo "Ahmed ToolBox browser runtime ready at $CDP_URL"
exec ahmed-toolbox
