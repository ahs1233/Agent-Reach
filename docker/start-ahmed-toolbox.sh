#!/bin/sh
set -eu

POT_PORT="${YTDLP_POT_PROVIDER_PORT:-4416}"
export YTDLP_POT_PROVIDER_URL="${YTDLP_POT_PROVIDER_URL:-http://127.0.0.1:${POT_PORT}}"

node /opt/bgutil-pot/server/build/main.js --host 127.0.0.1 --port "${POT_PORT}" &
pot_pid=$!

cleanup() {
  kill "${pot_pid}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Bound startup wait: do not start the public app with a silently dead provider.
attempt=0
until curl -fsS "${YTDLP_POT_PROVIDER_URL}/ping" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "${attempt}" -ge 30 ]; then
    echo "PO token provider failed to become healthy" >&2
    exit 1
  fi
  sleep 1
done

exec ahmed-toolbox
