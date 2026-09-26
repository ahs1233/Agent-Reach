"""Minimal read-only market-data MCP tools for OKX and OANDA."""

from __future__ import annotations

import math
import os
from typing import Any

import requests

_MAX_LOOKBACK = 2000
_MAX_RETURNED_TAIL = 500
_MAX_BOOK_DEPTH = 100
_TIMEOUT = float(os.environ.get("AHS_MARKET_HTTP_TIMEOUT_SECONDS", "15"))
_MA_PERIODS = (14, 22, 50, 200, 1000)

_OKX_BARS = {
    "M1": "1m",
    "M3": "3m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1H",
    "H2": "2H",
    "H4": "4H",
    "H6": "6H",
    "H12": "12H",
    "D1": "1D",
    "W1": "1W",
    "MN1": "1M",
}

_OANDA_BARS = {
    "S5": "S5",
    "S10": "S10",
    "S15": "S15",
    "S30": "S30",
    "M1": "M1",
    "M2": "M2",
    "M4": "M4",
    "M5": "M5",
    "M10": "M10",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H2": "H2",
    "H3": "H3",
    "H4": "H4",
    "H6": "H6",
    "H8": "H8",
    "H12": "H12",
    "D1": "D",
    "W1": "W",
    "MN1": "M",
}


def enabled() -> bool:
    return os.environ.get("AHS_MARKET_MCP_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def auth_token() -> str:
    return os.environ.get("AHS_MARKET_MCP_TOKEN", "").strip()


def _okx_base_url() -> str:
    return os.environ.get("OKX_BASE_URL", "https://www.okx.com").rstrip("/")


def _oanda_base_url() -> str:
    override = os.environ.get("OANDA_BASE_URL", "").strip()
    if override:
        return override.rstrip("/")
    env = os.environ.get("OANDA_ENV", "practice").strip().lower()
    return (
        "https://api-fxtrade.oanda.com"
        if env == "live"
        else "https://api-fxpractice.oanda.com"
    )


def _oanda_config() -> tuple[str, str]:
    token = os.environ.get("OANDA_API_TOKEN", "").strip()
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "").strip()
    if not token or not account_id:
        raise RuntimeError(
            "OANDA is not configured: set OANDA_API_TOKEN and OANDA_ACCOUNT_ID"
        )
    return token, account_id


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "market_snapshot",
            "description": (
                "Get a compact live market snapshot. Use OKX for crypto and OANDA "
                "for FX/metals such as XAU_USD. Read-only."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["provider", "symbol"],
                "properties": {
                    "provider": {"type": "string", "enum": ["okx", "oanda"]},
                    "symbol": {
                        "type": "string",
                        "description": (
                            "OKX example BTC-USDT; OANDA example XAU_USD or EUR_USD."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "market_candles",
            "description": (
                "Get a compact technical-analysis packet built from up to 2000 "
                "live/historical OHLCV candles. The backend computes indicators "
                "locally and returns only a bounded recent tail plus summary."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["provider", "symbol", "timeframe"],
                "properties": {
                    "provider": {"type": "string", "enum": ["okx", "oanda"]},
                    "symbol": {"type": "string"},
                    "timeframe": {
                        "type": "string",
                        "description": (
                            "Common values: M1, M5, M15, M30, H1, H4, D1, W1."
                        ),
                    },
                    "lookback": {
                        "type": "integer",
                        "minimum": 50,
                        "maximum": 2000,
                        "default": 1200,
                        "description": (
                            "Candles used internally for indicators and structure."
                        ),
                    },
                    "tail": {
                        "type": "integer",
                        "minimum": 20,
                        "maximum": 500,
                        "default": 200,
                        "description": (
                            "Most recent candles returned to the model after local "
                            "calculation."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "description": (
                            "Legacy compatibility. If supplied without lookback, it "
                            "sets both internal lookback and returned tail."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "market_orderbook",
            "description": (
                "Get OKX public order-book depth for crypto. "
                "Read-only. OANDA is intentionally not exposed here."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Example: BTC-USDT.",
                    },
                    "depth": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
                "additionalProperties": False,
            },
        },
    ]


def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "enabled": enabled(),
        "okx": True,
        "oanda_configured": bool(
            os.environ.get("OANDA_API_TOKEN", "").strip()
            and os.environ.get("OANDA_ACCOUNT_ID", "").strip()
        ),
        "auth_enabled": bool(auth_token()),
        "max_lookback": _MAX_LOOKBACK,
        "max_returned_tail": _MAX_RETURNED_TAIL,
        "tools": [spec["name"] for spec in tool_specs()],
    }


def _get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("provider returned a non-object response")
    return data


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _okx_snapshot(symbol: str) -> dict[str, Any]:
    data = _get_json(
        f"{_okx_base_url()}/api/v5/market/ticker",
        params={"instId": symbol},
    )
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"OKX returned no ticker for {symbol}")
    row = rows[0]
    return {
        "provider": "okx",
        "symbol": symbol,
        "timestamp_ms": int(row.get("ts") or 0),
        "last": _to_float(row.get("last")),
        "bid": _to_float(row.get("bidPx")),
        "ask": _to_float(row.get("askPx")),
        "open_24h": _to_float(row.get("open24h")),
        "high_24h": _to_float(row.get("high24h")),
        "low_24h": _to_float(row.get("low24h")),
        "volume_24h": _to_float(row.get("vol24h")),
        "volume_quote_24h": _to_float(row.get("volCcy24h")),
    }


def _parse_okx_row(row: list[Any]) -> dict[str, Any]:
    return {
        "t": int(row[0]),
        "o": _to_float(row[1]),
        "h": _to_float(row[2]),
        "l": _to_float(row[3]),
        "c": _to_float(row[4]),
        "v": _to_float(row[5]) if len(row) > 5 else None,
        "closed": str(row[8]) == "1" if len(row) > 8 else None,
    }


def _okx_candle_rows(
    symbol: str,
    timeframe: str,
    lookback: int,
) -> list[dict[str, Any]]:
    bar = _OKX_BARS.get(timeframe.upper())
    if not bar:
        raise RuntimeError(f"unsupported OKX timeframe: {timeframe}")

    requested = max(1, min(int(lookback), _MAX_LOOKBACK))
    recent_limit = min(300, requested)
    recent = _get_json(
        f"{_okx_base_url()}/api/v5/market/candles",
        params={"instId": symbol, "bar": bar, "limit": recent_limit},
    )
    rows = recent.get("data") or []
    parsed_by_ts = {
        int(row[0]): _parse_okx_row(row)
        for row in rows
        if isinstance(row, list) and len(row) >= 5
    }

    while len(parsed_by_ts) < requested and parsed_by_ts:
        oldest = min(parsed_by_ts)
        remaining = requested - len(parsed_by_ts)
        page_limit = min(300, remaining)
        history = _get_json(
            f"{_okx_base_url()}/api/v5/market/history-candles",
            params={
                "instId": symbol,
                "bar": bar,
                "after": str(oldest),
                "limit": page_limit,
            },
        )
        page = history.get("data") or []
        before_count = len(parsed_by_ts)
        for row in page:
            if isinstance(row, list) and len(row) >= 5:
                parsed_by_ts[int(row[0])] = _parse_okx_row(row)
        if not page or len(parsed_by_ts) == before_count:
            break

    ordered = [parsed_by_ts[ts] for ts in sorted(parsed_by_ts)]
    return ordered[-requested:]


def _okx_orderbook(symbol: str, depth: int) -> dict[str, Any]:
    depth = max(1, min(int(depth), _MAX_BOOK_DEPTH))
    data = _get_json(
        f"{_okx_base_url()}/api/v5/market/books",
        params={"instId": symbol, "sz": depth},
    )
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"OKX returned no order book for {symbol}")
    row = rows[0]
    return {
        "provider": "okx",
        "symbol": symbol,
        "timestamp_ms": int(row.get("ts") or 0),
        "bids": row.get("bids") or [],
        "asks": row.get("asks") or [],
    }


def _oanda_headers() -> tuple[dict[str, str], str]:
    token, account_id = _oanda_config()
    return (
        {
            "Authorization": f"Bearer {token}",
            "Accept-Datetime-Format": "RFC3339",
        },
        account_id,
    )


def _oanda_snapshot(instrument: str) -> dict[str, Any]:
    headers, account_id = _oanda_headers()
    data = _get_json(
        f"{_oanda_base_url()}/v3/accounts/{account_id}/pricing",
        params={"instruments": instrument},
        headers=headers,
    )
    prices = data.get("prices") or []
    if not prices:
        raise RuntimeError(f"OANDA returned no price for {instrument}")
    price = prices[0]
    bids = price.get("bids") or []
    asks = price.get("asks") or []
    return {
        "provider": "oanda",
        "instrument": instrument,
        "time": price.get("time"),
        "status": price.get("status"),
        "bid": _to_float(bids[0].get("price")) if bids else None,
        "ask": _to_float(asks[0].get("price")) if asks else None,
        "closeout_bid": _to_float(price.get("closeoutBid")),
        "closeout_ask": _to_float(price.get("closeoutAsk")),
    }


def _oanda_candle_rows(
    instrument: str,
    timeframe: str,
    lookback: int,
) -> list[dict[str, Any]]:
    headers, account_id = _oanda_headers()
    granularity = _OANDA_BARS.get(timeframe.upper())
    if not granularity:
        raise RuntimeError(f"unsupported OANDA timeframe: {timeframe}")
    requested = max(1, min(int(lookback), _MAX_LOOKBACK))
    data = _get_json(
        (
            f"{_oanda_base_url()}/v3/accounts/{account_id}/instruments/"
            f"{instrument}/candles"
        ),
        params={
            "granularity": granularity,
            "count": requested,
            "price": "M",
        },
        headers=headers,
    )
    candles: list[dict[str, Any]] = []
    for row in data.get("candles") or []:
        mid = row.get("mid") or {}
        candles.append(
            {
                "t": row.get("time"),
                "o": _to_float(mid.get("o")),
                "h": _to_float(mid.get("h")),
                "l": _to_float(mid.get("l")),
                "c": _to_float(mid.get("c")),
                "v": _to_float(row.get("volume")),
                "closed": bool(row.get("complete")),
            }
        )
    return candles[-requested:]


def _valid_closes(candles: list[dict[str, Any]]) -> list[float]:
    return [
        float(candle["c"])
        for candle in candles
        if isinstance(candle.get("c"), (int, float))
    ]


def _sma(values: list[float], period: int, *, end: int | None = None) -> float | None:
    stop = len(values) if end is None else end
    if stop < period:
        return None
    window = values[stop - period : stop]
    if len(window) != period:
        return None
    return sum(window) / period


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


def _rsi_wilder(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(deltas)):
        avg_gain = ((avg_gain * (period - 1)) + gains[index]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[index]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr_wilder(
    candles: list[dict[str, Any]],
    period: int = 14,
) -> float | None:
    usable = [
        candle
        for candle in candles
        if all(
            isinstance(candle.get(key), (int, float))
            for key in ("h", "l", "c")
        )
    ]
    if len(usable) <= period:
        return None

    true_ranges: list[float] = []
    for index in range(1, len(usable)):
        high = float(usable[index]["h"])
        low = float(usable[index]["l"])
        previous_close = float(usable[index - 1]["c"])
        true_ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((atr * (period - 1)) + true_range) / period
    return atr


def _pct_change(values: list[float], bars: int) -> float | None:
    if len(values) <= bars or values[-bars - 1] == 0:
        return None
    return ((values[-1] / values[-bars - 1]) - 1.0) * 100.0


def _range_summary(
    candles: list[dict[str, Any]],
    bars: int,
) -> dict[str, float] | None:
    subset = candles[-bars:]
    highs = [
        float(candle["h"])
        for candle in subset
        if isinstance(candle.get("h"), (int, float))
    ]
    lows = [
        float(candle["l"])
        for candle in subset
        if isinstance(candle.get("l"), (int, float))
    ]
    if not highs or not lows:
        return None
    return {"high": max(highs), "low": min(lows)}


def _round(value: float | None, digits: int = 8) -> float | None:
    return None if value is None else round(value, digits)


def _technical_summary(candles: list[dict[str, Any]]) -> dict[str, Any]:
    closes = _valid_closes(candles)
    last_close = closes[-1] if closes else None

    sma: dict[str, float | None] = {}
    ema: dict[str, float | None] = {}
    distance_from_sma_pct: dict[str, float | None] = {}
    sma_slope_5_pct: dict[str, float | None] = {}

    for period in _MA_PERIODS:
        current_sma = _sma(closes, period)
        current_ema = _ema(closes, period)
        previous_sma = _sma(closes, period, end=len(closes) - 5)
        key = str(period)
        sma[key] = _round(current_sma)
        ema[key] = _round(current_ema)
        if (
            last_close is not None
            and current_sma not in (None, 0)
        ):
            distance_from_sma_pct[key] = _round(
                ((last_close / current_sma) - 1.0) * 100.0,
                5,
            )
        else:
            distance_from_sma_pct[key] = None
        if current_sma not in (None, 0) and previous_sma is not None:
            sma_slope_5_pct[key] = _round(
                ((current_sma / previous_sma) - 1.0) * 100.0,
                5,
            )
        else:
            sma_slope_5_pct[key] = None

    forming = (
        candles[-1]
        if candles and candles[-1].get("closed") is False
        else None
    )

    return {
        "last_close": _round(last_close),
        "rsi14": _round(_rsi_wilder(closes), 4),
        "atr14": _round(_atr_wilder(candles)),
        "sma": sma,
        "ema": ema,
        "distance_from_sma_pct": distance_from_sma_pct,
        "sma_slope_5_pct": sma_slope_5_pct,
        "change_pct": {
            "1_bar": _round(_pct_change(closes, 1), 5),
            "5_bar": _round(_pct_change(closes, 5), 5),
            "20_bar": _round(_pct_change(closes, 20), 5),
        },
        "range": {
            "20": _range_summary(candles, 20),
            "50": _range_summary(candles, 50),
            "200": _range_summary(candles, 200),
        },
        "forming_candle": (
            {
                "t": forming.get("t"),
                "o": forming.get("o"),
                "h": forming.get("h"),
                "l": forming.get("l"),
                "c": forming.get("c"),
                "v": forming.get("v"),
            }
            if forming is not None
            else None
        ),
    }


def _compact_packet(
    provider: str,
    symbol: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    *,
    lookback: int,
    tail: int,
) -> dict[str, Any]:
    returned_tail = max(1, min(int(tail), _MAX_RETURNED_TAIL))
    recent = candles[-returned_tail:]
    packed = [
        [
            candle.get("t"),
            candle.get("o"),
            candle.get("h"),
            candle.get("l"),
            candle.get("c"),
            candle.get("v"),
            candle.get("closed"),
        ]
        for candle in recent
    ]
    return {
        "provider": provider,
        "symbol": symbol,
        "timeframe": timeframe.upper(),
        "analysis_candles": len(candles),
        "requested_lookback": lookback,
        "returned_candles": len(recent),
        "columns": ["t", "o", "h", "l", "c", "v", "closed"],
        "indicators": _technical_summary(candles),
        "candles": packed,
    }


def _resolve_candle_window(arguments: dict[str, Any]) -> tuple[int, int]:
    legacy_limit = arguments.get("limit")
    if legacy_limit is not None and "lookback" not in arguments:
        value = max(1, min(int(legacy_limit), _MAX_RETURNED_TAIL))
        return value, value

    lookback = max(
        50,
        min(int(arguments.get("lookback") or 1200), _MAX_LOOKBACK),
    )
    tail = max(
        20,
        min(int(arguments.get("tail") or 200), _MAX_RETURNED_TAIL),
    )
    return lookback, min(tail, lookback)


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "market_snapshot":
        provider = str(arguments.get("provider") or "").lower()
        symbol = str(arguments.get("symbol") or "").strip()
        if not symbol:
            raise ValueError("symbol is required")
        if provider == "okx":
            return _okx_snapshot(symbol)
        if provider == "oanda":
            return _oanda_snapshot(symbol)
        raise ValueError("provider must be okx or oanda")

    if name == "market_candles":
        provider = str(arguments.get("provider") or "").lower()
        symbol = str(arguments.get("symbol") or "").strip()
        timeframe = str(arguments.get("timeframe") or "").upper()
        if not symbol:
            raise ValueError("symbol is required")
        lookback, tail = _resolve_candle_window(arguments)
        if provider == "okx":
            candles = _okx_candle_rows(symbol, timeframe, lookback)
        elif provider == "oanda":
            candles = _oanda_candle_rows(symbol, timeframe, lookback)
        else:
            raise ValueError("provider must be okx or oanda")
        return _compact_packet(
            provider,
            symbol,
            timeframe,
            candles,
            lookback=lookback,
            tail=tail,
        )

    if name == "market_orderbook":
        symbol = str(arguments.get("symbol") or "").strip()
        if not symbol:
            raise ValueError("symbol is required")
        return _okx_orderbook(symbol, int(arguments.get("depth") or 20))

    raise ValueError(f"unknown market tool: {name}")
