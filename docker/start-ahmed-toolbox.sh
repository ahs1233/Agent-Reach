#!/bin/sh
set -eu

POT_PORT="${YTDLP_POT_PROVIDER_PORT:-4416}"
export YTDLP_POT_PROVIDER_URL="${YTDLP_POT_PROVIDER_URL:-http://127.0.0.1:${POT_PORT}}"

PORT="${POT_PORT}" HOST=127.0.0.1 node /opt/bgutil-pot/server/build/main.js &
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

if [ "${AHMED_VIDEO_LIVE_ACCEPTANCE:-0}" = "1" ]; then
  echo "Running Ahmed Video Intelligence live acceptance..." >&2
  /opt/venv/bin/python -m agent_reach.toolbox.video_live_acceptance
fi

exec ahmed-toolbox
