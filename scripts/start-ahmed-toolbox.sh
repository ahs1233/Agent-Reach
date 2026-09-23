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

if ! timeout 30s browser-use >/tmp/ahmed-browser-use-smoke.log 2>&1 <<'PY'
info = page_info()
print(info)
PY
then
  echo "Browser Use CLI could not attach to Chromium CDP" >&2
  cat /tmp/ahmed-browser-use-smoke.log >&2 || true
  exit 1
fi

echo "Ahmed ToolBox Browser Use CLI/CDP smoke test passed"

if [ -n "${AHMED_TOOLBOX_BROWSER_E2E_URL:-}" ]; then
  echo "Running Ahmed ToolBox YouTube Browser Use E2E acceptance"
  python - <<'PY'
import json
import os

from agent_reach.toolbox.gateway import AhmedToolboxGateway

url = os.environ["AHMED_TOOLBOX_BROWSER_E2E_URL"]
gateway = AhmedToolboxGateway.from_environment()
tool_names = {tool["name"] for tool in gateway.list_tools()}
if "reach_youtube_browser_inspect" not in tool_names:
    raise SystemExit("reach_youtube_browser_inspect missing from gateway tool list")

def call(include_comments: bool, timeout_seconds: int) -> dict:
    result = gateway.call_tool(
        "reach_youtube_browser_inspect",
        {
            "url": url,
            "include_comments": include_comments,
            "max_comments": 5,
            "timeout_seconds": timeout_seconds,
        },
    )
    if result.get("isError"):
        text = (result.get("content") or [{}])[0].get("text", "unknown tool error")
        raise SystemExit(text)
    content = result.get("content") or []
    if not content or not isinstance(content[0], dict):
        raise SystemExit("YouTube browser inspection returned no text content")
    payload = json.loads(content[0].get("text") or "{}")
    return {
        "source_url": payload.get("source_url", ""),
        "resolved_url": payload.get("resolved_url", ""),
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
        "visible_text": str(payload.get("visible_text", ""))[:3000],
        "comments": payload.get("comments") or [],
        "retrieval_tool": payload.get("retrieval_tool", ""),
        "mode": payload.get("mode", ""),
    }

without_comments = call(False, 75)
if not without_comments["title"] or not without_comments["visible_text"]:
    raise SystemExit("YouTube Browser Use E2E returned empty title or visible text")
print(
    "__AHMED_TOOLBOX_YOUTUBE_E2E_NO_COMMENTS__"
    + json.dumps(without_comments, ensure_ascii=False)
)

with_comments = call(True, 90)
if not with_comments["title"] or not with_comments["visible_text"]:
    raise SystemExit("YouTube Browser Use comments E2E returned empty title or visible text")
if not with_comments["comments"]:
    raise SystemExit("YouTube Browser Use E2E loaded no visible comments")
print(
    "__AHMED_TOOLBOX_YOUTUBE_E2E_WITH_COMMENTS__"
    + json.dumps(with_comments, ensure_ascii=False)
)
PY
  echo "Ahmed ToolBox YouTube Browser Use E2E acceptance passed"
fi

exec ahmed-toolbox
