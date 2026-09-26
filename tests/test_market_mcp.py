from __future__ import annotations

from typing import Any

from agent_reach.toolbox import market_mcp


class _Response:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


def test_market_specs_are_minimal_and_read_only() -> None:
    names = [item["name"] for item in market_mcp.tool_specs()]
    assert names == ["market_snapshot", "market_candles", "market_orderbook"]


def test_okx_snapshot(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        return _Response(
            {
                "data": [
                    {
                        "ts": "123",
                        "last": "100",
                        "bidPx": "99",
                        "askPx": "101",
                        "open24h": "95",
                        "high24h": "105",
                        "low24h": "90",
                        "vol24h": "42",
                        "volCcy24h": "4200",
                    }
                ]
            }
        )

    monkeypatch.setattr(market_mcp.requests, "get", fake_get)
    result = market_mcp.call_tool(
        "market_snapshot",
        {"provider": "okx", "symbol": "BTC-USDT"},
    )
    assert result["provider"] == "okx"
    assert result["last"] == "100"
    assert result["bid"] == "99"
    assert result["ask"] == "101"


def test_okx_candles_are_chronological(monkeypatch) -> None:
    def fake_get(*args, **kwargs):
        return _Response(
            {
                "data": [
                    ["2000", "2", "4", "1", "3", "20", "0", "0", "1"],
                    ["1000", "1", "3", "0", "2", "10", "0", "0", "1"],
                ]
            }
        )

    monkeypatch.setattr(market_mcp.requests, "get", fake_get)
    result = market_mcp.call_tool(
        "market_candles",
        {
            "provider": "okx",
            "symbol": "BTC-USDT",
            "timeframe": "M1",
            "limit": 2,
        },
    )
    assert [item["t"] for item in result["candles"]] == [1000, 2000]


def test_oanda_requires_secret_configuration(monkeypatch) -> None:
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)

    try:
        market_mcp.call_tool(
            "market_snapshot",
            {"provider": "oanda", "symbol": "XAU_USD"},
        )
    except RuntimeError as exc:
        assert "OANDA is not configured" in str(exc)
    else:
        raise AssertionError("expected missing OANDA configuration to fail")
