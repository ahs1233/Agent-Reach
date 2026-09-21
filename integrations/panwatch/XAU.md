# XAUUSD integration design

## Identity rule

Do not collapse spot gold and the research proxy into one price identity:

```text
user/execution instrument = XAUUSD
TradingAgents/Yahoo proxy = GC=F
```

TradingAgents v0.5.0 already normalizes `XAUUSD` to `GC=F`. The supplied
`YahooGoldResearchProvider` makes the same relationship explicit and marks
every returned bar `execution_eligible=False`.

A broker/spot-feed adapter must provide `XAUQuote(... execution_eligible=True)`
and, preferably, spot `XAUBar` values before live execution is considered.

## Files to copy into PanWatch

```text
integrations/panwatch/src/platform/marketdata/xau_models.py
→ src/platform/marketdata/xau_models.py

integrations/panwatch/src/platform/marketdata/xau_research_provider.py
→ src/platform/marketdata/xau_research_provider.py

integrations/panwatch/src/modules/strategy/xau_intraday.py
→ src/modules/strategy/xau_intraday.py

integrations/panwatch/src/modules/automation/tradingagents/xau_support.py
→ src/modules/automation/tradingagents/xau_support.py
```

## Fast brain

`XAUIntradayEngine` evaluates 1m/5m/15m inputs without an LLM. It computes:

- EMA 9 / EMA 21
- RSI 14
- ATR 14
- 20-bar breakout state
- recent swing high/low
- stale-data gates
- quote freshness
- optional spread gate
- high-impact-event gate
- optional macro bias conflict

It returns `long_setup`, `short_setup`, or `none` as a candidate state.
It does not place a trade and does not invent a fixed stop-loss/target. The
returned ATR and swing references are the inputs for a later risk policy.

For any execution-oriented caller, instantiate it with
`require_execution_data=True`. That mode blocks research-proxy bars
(`execution_eligible=False`) and requires a fresh execution-eligible spot
quote. Macro disagreement is reported separately as a warning rather than
being mislabeled as a hard data/risk gate.

## TradingAgents slow brain

For XAU runs, use:

```python
from src.modules.automation.tradingagents.xau_support import (
    configure_xau_tradingagents,
    propagate_xau,
)

ta_config = configure_xau_tradingagents(ta_config)
final_state, decision = propagate_xau(
    graph,
    date_str,
    portfolio=to_tradingagents_portfolio(portfolio),
)
```

This removes the company Fundamentals Analyst and passes
`asset_type="commodity"`. TradingAgents v0.5.0's public CLI documents stock
and crypto modes, but its programmatic `propagate(..., asset_type=...)` path
accepts the value and downstream researcher prompts treat non-stock assets
generically. Treat `commodity` as a PanWatch compatibility extension until
the integration test suite has exercised a full XAU run. Market, sentiment,
news, bull/bear research, trader, risk debate, and portfolio manager remain
available.

## Atria

No special Responses adapter is required for the current Atria service.
Atria-Dawn-Preview officially supports standard OpenAI-compatible
`/v1/chat/completions`, including streaming. PanWatch's existing `AIClient`
therefore fits it directly:

```text
base_url = https://api.atria-asi.ai/v1
model    = Atria-Dawn-Preview
api_key  = <ATRIA_API_KEY>
```

Keep the model text-only. Do not send images/PDF binary content through this
provider.

TradingAgents may use the same endpoint through PanWatch's existing
OpenAI-compatible bridge. Validate tool calling on a representative paper run
before making it the sole model in the failover chain.
