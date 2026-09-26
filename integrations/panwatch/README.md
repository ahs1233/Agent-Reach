# PanWatch ↔ Ahmed ToolBox integration

This directory contains drop-in source files for PanWatch. It is kept outside
the Agent Reach package because the connected GitHub account does not currently
have a writable PanWatch fork.

## 1. Start Scrapling MCP

Use authentication whenever the server is reachable over a network:

```bash
export SCRAPLING_MCP_AUTH_TOKEN="<random-secret>"
scrapling-mcp --http --host 127.0.0.1 --port 8000
```

## 2. Start Ahmed ToolBox

The gateway exposes Agent Reach tools and only an allowlisted read-only subset
of Scrapling by default.

```bash
export AHMED_TOOLBOX_TOKEN="<another-random-secret>"
export SCRAPLING_TOKEN="<random-secret>"
export AHMED_TOOLBOX_REMOTE_MCPS='{
  "scrapling": {
    "url": "http://127.0.0.1:8000/mcp",
    "token_env": "SCRAPLING_TOKEN"
  }
}'
ahmed-toolbox
```

Default endpoint: `http://127.0.0.1:8765/mcp`.

The Scrapling default allowlist is:
- `bulk_get`
- `fetch`
- `bulk_fetch`
- `stealthy_fetch`
- `bulk_stealthy_fetch`

`make_request` is deliberately excluded because it can POST/PUT/DELETE.
Session tools are also excluded by default.

## 3. Copy the PanWatch adapter

Copy:

```text
integrations/panwatch/src/platform/external_tools/
→ PanWatch/src/platform/external_tools/
```

## 4. Add settings

Add to `src/platform/runtime/config.py::Settings`:

```python
ahmed_toolbox_url: str = ""
ahmed_toolbox_token: str = ""
ahmed_toolbox_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
```

Environment variables become:

```bash
AHMED_TOOLBOX_URL=http://127.0.0.1:8765/mcp
AHMED_TOOLBOX_TOKEN=<secret>
AHMED_TOOLBOX_TIMEOUT_SECONDS=5
```

## 5. Register the tools in AssistantService

In `src/modules/assistant/service.py`, import:

```python
from src.platform.external_tools import (
    AhmedToolboxClient,
    register_ahmed_toolbox_tools,
)
```

Then change `build_runtime` from the current static descriptor composition to:

```python
def build_runtime(self, failover_client) -> AgentRuntime:
    tools = build_panwatch_tool_registry(self._repository.session)
    descriptors = list(PANWATCH_TOOL_DESCRIPTORS)

    if self._settings.ahmed_toolbox_url:
        try:
            external_descriptors = register_ahmed_toolbox_tools(
                tools,
                AhmedToolboxClient(
                    self._settings.ahmed_toolbox_url,
                    token=self._settings.ahmed_toolbox_token,
                    timeout_seconds=self._settings.ahmed_toolbox_timeout_seconds,
                ),
            )
            descriptors.extend(external_descriptors)
        except Exception:
            # External research must fail soft; PanWatch's local tools remain usable.
            pass

    return AgentRuntime(
        FailoverModelAdapter(failover_client),
        tools,
        policy=self.build_tool_policy(),
        extensions=(
            [
                ToolResearchPlugin(
                    ToolResearchService(tools, descriptors=descriptors),
                    mode="active",
                )
            ]
            if self._settings.tool_research_enabled
            else []
        ),
    )
```

The new tools are `DEFERRED`, so they do not expand every model turn's schema.
The active Tool Research plugin discovers them only when the query needs them.

## 6. Security boundary

Do not connect PanWatch directly to an unrestricted browser automation server.
The intended trust chain is:

```text
PanWatch ToolPolicy
    ↓
Ahmed ToolBox allowlist
    ↓
Scrapling/Patchright MCP
```

PanWatch treats only the gateway's allowlisted surface as read-only.
