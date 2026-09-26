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
    assert result["last"] == 100.0
    assert result["bid"] == 99.0
    assert result["ask"] == 101.0


def test_okx_candles_are_chronological_and_compact(monkeypatch) -> None:
    def fake_get(url, **kwargs):
        if url.endswith("/market/candles"):
            return _Response(
                {
                    "data": [
                        ["2000", "2", "4", "1", "3", "20", "0", "0", "0"],
                        ["1000", "1", "3", "0", "2", "10", "0", "0", "1"],
                    ]
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

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
    assert result["columns"] == ["t", "o", "h", "l", "c", "v", "closed"]
    assert [item[0] for item in result["candles"]] == [1000, 2000]
    assert result["candles"][-1][-1] is False
    assert result["indicators"]["forming_candle"]["t"] == 2000


def test_okx_lookback_paginates_history(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url, **kwargs):
        params = kwargs.get("params") or {}
        calls.append((url, params))
        if url.endswith("/market/candles"):
            rows = [
                [str(ts), "1", "2", "0", str(ts / 1000), "1", "0", "0", "1"]
                for ts in (5000, 4000, 3000)
            ]
            return _Response({"data": rows})
        if url.endswith("/market/history-candles"):
            rows = [
                [str(ts), "1", "2", "0", str(ts / 1000), "1", "0", "0", "1"]
                for ts in (2000, 1000)
            ]
            return _Response({"data": rows})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(market_mcp.requests, "get", fake_get)
    rows = market_mcp._okx_candle_rows("BTC-USDT", "M1", 5)

    assert [item["t"] for item in rows] == [1000, 2000, 3000, 4000, 5000]
    history_calls = [item for item in calls if item[0].endswith("history-candles")]
    assert history_calls
    assert history_calls[0][1]["after"] == "3000"


def test_compact_packet_calculates_local_indicators() -> None:
    candles = []
    for index in range(1, 1201):
        close = 100.0 + (index * 0.1)
        candles.append(
            {
                "t": index,
                "o": close - 0.05,
                "h": close + 0.2,
                "l": close - 0.2,
                "c": close,
                "v": 10.0,
                "closed": index != 1200,
            }
        )

    result = market_mcp._compact_packet(
        "okx",
        "BTC-USDT",
        "M1",
        candles,
        lookback=1200,
        tail=200,
    )

    assert result["analysis_candles"] == 1200
    assert result["returned_candles"] == 200
    assert result["indicators"]["sma"]["1000"] is not None
    assert result["indicators"]["ema"]["1000"] is not None
    assert result["indicators"]["rsi14"] == 100.0
    assert result["indicators"]["forming_candle"]["t"] == 1200


def test_default_window_supports_ma1000() -> None:
    lookback, tail = market_mcp._resolve_candle_window({})
    assert lookback == 1200
    assert tail == 200


def test_window_is_bounded() -> None:
    lookback, tail = market_mcp._resolve_candle_window(
        {"lookback": 99999, "tail": 99999}
    )
    assert lookback == 2000
    assert tail == 500


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



def test_oanda_discovers_single_account_from_token(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_API_TOKEN", "secret-token")
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)

    def fake_get(url, **kwargs):
        if url.endswith("/v3/accounts"):
            headers = kwargs.get("headers") or {}
            assert headers["Authorization"] == "Bearer secret-token"
            return _Response({"accounts": [{"id": "101-001-123"}]})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(market_mcp.requests, "get", fake_get)
    assert market_mcp._oanda_account_id() == "101-001-123"


def test_oanda_requires_explicit_account_when_token_has_multiple(monkeypatch) -> None:
    monkeypatch.setenv("OANDA_API_TOKEN", "secret-token")
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)

    def fake_get(url, **kwargs):
        if url.endswith("/v3/accounts"):
            return _Response(
                {
                    "accounts": [
                        {"id": "101-001-123"},
                        {"id": "101-001-456"},
                    ]
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(market_mcp.requests, "get", fake_get)
    try:
        market_mcp._oanda_account_id()
    except RuntimeError as exc:
        assert "multiple accounts" in str(exc)
    else:
        raise AssertionError("expected multi-account token to require OANDA_ACCOUNT_ID")
