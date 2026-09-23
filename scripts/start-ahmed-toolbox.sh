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

case "${AHMED_RUNTIME_ENABLED:-0}:${AHMED_ORCHESTRATION_ENABLED:-0}" in
  1:*|true:*|yes:*|on:*|*:1|*:true|*:yes|*:on)
    echo "Running Ahmed Runtime startup acceptance"
    python - <<'PY'
import json
import os

from agent_reach.toolbox.gateway import AhmedToolboxGateway

gateway = AhmedToolboxGateway.from_environment()
names = {tool["name"] for tool in gateway.list_tools()}
required = {
    "runtime_status",
    "runtime_execute_workflow",
    "runtime_delegate",
    "runtime_memory_put",
    "runtime_memory_search",
    "runtime_session_search",
    "runtime_skill_save",
    "runtime_skill_list",
    "runtime_skill_get",
    "runtime_skill_rollback",
    "runtime_skill_execute",
}
missing = sorted(required - names)
if missing:
    raise SystemExit(f"Ahmed Runtime tools missing: {missing}")

def payload(name, arguments):
    result = gateway.call_tool(name, arguments)
    if result.get("isError"):
        raise SystemExit(f"{name} failed: {result}")
    content = result.get("content") or []
    if not content:
        raise SystemExit(f"{name} returned no content")
    return json.loads(content[0]["text"])

status = payload("runtime_status", {})
if status.get("status") != "ok":
    raise SystemExit(f"runtime_status unhealthy: {status}")

workflow = payload(
    "runtime_execute_workflow",
    {
        "session_id": "__startup_runtime__",
        "steps": [{"id": "doctor", "tool_name": "reach_doctor", "arguments": {}}],
        "learn_as": "__startup_doctor_skill__",
        "skill_description": "Railway startup acceptance health check.",
    },
)
if workflow.get("status") != "ok":
    raise SystemExit(f"runtime workflow smoke failed: {workflow}")

skill = payload("runtime_skill_execute", {"name": "__startup_doctor_skill__"})
if skill.get("status") != "ok":
    raise SystemExit(f"runtime skill smoke failed: {skill}")

payload(
    "runtime_memory_put",
    {
        "key": "__startup_runtime_memory__",
        "content": "Ahmed Runtime startup acceptance passed.",
        "tags": ["startup", "runtime"],
    },
)
memory = payload("runtime_memory_search", {"query": "startup acceptance", "limit": 5})
if not memory.get("results"):
    raise SystemExit("runtime memory smoke returned no results")

if "orchestration_start" in names:
    started = payload(
        "orchestration_start",
        {
            "objective": "Railway startup runtime/orchestration acceptance",
            "mode": "general",
            "budget": {"tool_calls": 1, "network_calls": 0},
        },
    )
    oid = started["orchestration_id"]
    orchestrated = payload(
        "runtime_execute_workflow",
        {
            "orchestration_id": oid,
            "session_id": "__startup_orchestration__",
            "steps": [
                {
                    "id": "doctor",
                    "tool_name": "reach_doctor",
                    "role": "orchestrator",
                    "arguments": {},
                }
            ],
        },
    )
    if orchestrated.get("status") != "ok":
        raise SystemExit(f"orchestrated runtime smoke failed: {orchestrated}")
    verification = payload("orchestration_verify", {"orchestration_id": oid})
    if not verification.get("passed"):
        raise SystemExit(f"orchestration verification failed: {verification}")
    completed = payload("orchestration_complete", {"orchestration_id": oid})
    if not completed.get("completed"):
        raise SystemExit(f"orchestration completion failed: {completed}")

provider_keys = (
    "AHMED_SUBAGENT_BASE_URL",
    "AHMED_SUBAGENT_API_KEY",
    "AHMED_SUBAGENT_MODEL",
)
provider_values = [bool(os.environ.get(key, "").strip()) for key in provider_keys]
if any(provider_values) and not all(provider_values):
    raise SystemExit("Ahmed model-subagent provider configuration is incomplete")
if all(provider_values):
    if not status.get("features", {}).get("model_subagents"):
        raise SystemExit("runtime_status did not enable model_subagents")
    parent = payload(
        "orchestration_start",
        {
            "objective": "Railway live model-subagent acceptance",
            "mode": "general",
            "budget": {"tool_calls": 1, "network_calls": 0},
        },
    )
    delegated = payload(
        "runtime_delegate",
        {
            "orchestration_id": parent["orchestration_id"],
            "max_parallel": 1,
            "timeout_seconds": 120,
            "tasks": [
                {
                    "id": "live-model-child",
                    "objective": "Verify model-backed child tool calling.",
                    "goal": (
                        "You MUST call reach_doctor exactly once using the provided tool. "
                        "After the tool returns, reply with one short sentence confirming the check."
                    ),
                    "role": "orchestrator",
                    "tool_allowlist": ["reach_doctor"],
                    "max_turns": 4,
                    "budget": {"tool_calls": 1, "network_calls": 0},
                }
            ],
        },
    )
    if delegated.get("status") != "ok":
        raise SystemExit(f"model-subagent delegation failed: {delegated}")
    tasks = delegated.get("tasks") or []
    if not tasks or tasks[0].get("status") != "ok":
        raise SystemExit(f"model-subagent task failed: {delegated}")
    child_result = tasks[0].get("result") or {}
    if int(child_result.get("tool_call_count") or 0) < 1:
        raise SystemExit(
            "model-subagent provider responded without a verified tool call"
        )
    child_id = child_result.get("child_orchestration_id")
    child_status = payload(
        "orchestration_status", {"orchestration_id": child_id}
    )
    if child_status.get("usage", {}).get("tool_calls") != 1:
        raise SystemExit(
            f"model-subagent tool budget/journal mismatch: {child_status}"
        )
    print("__AHMED_MODEL_SUBAGENT_STARTUP_ACCEPTANCE__ok")

print("__AHMED_RUNTIME_STARTUP_ACCEPTANCE__ok")
PY
    echo "Ahmed Runtime startup acceptance passed"
    ;;
esac

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
