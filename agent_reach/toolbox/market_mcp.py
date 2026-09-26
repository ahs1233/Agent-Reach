"""Minimal read-only market-data MCP tools for OKX and OANDA."""

from __future__ import annotations

import os
from typing import Any

import requests

_MAX_CANDLES = 300
_MAX_BOOK_DEPTH = 100
_TIMEOUT = float(os.environ.get("AHS_MARKET_HTTP_TIMEOUT_SECONDS", "15"))

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
                "Get OHLC/volume chart candles for technical analysis. "
                "Read-only and bounded to 300 candles."
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
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                        "default": 120,
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
        "last": row.get("last"),
        "bid": row.get("bidPx"),
        "ask": row.get("askPx"),
        "open_24h": row.get("open24h"),
        "high_24h": row.get("high24h"),
        "low_24h": row.get("low24h"),
        "volume_24h": row.get("vol24h"),
        "volume_quote_24h": row.get("volCcy24h"),
    }


def _okx_candles(symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
    bar = _OKX_BARS.get(timeframe.upper())
    if not bar:
        raise RuntimeError(f"unsupported OKX timeframe: {timeframe}")
    limit = max(1, min(int(limit), _MAX_CANDLES))
    data = _get_json(
        f"{_okx_base_url()}/api/v5/market/candles",
        params={"instId": symbol, "bar": bar, "limit": limit},
    )
    candles: list[dict[str, Any]] = []
    for row in reversed(data.get("data") or []):
        candles.append(
            {
                "t": int(row[0]),
                "o": row[1],
                "h": row[2],
                "l": row[3],
                "c": row[4],
                "v": row[5] if len(row) > 5 else None,
                "confirm": row[8] if len(row) > 8 else None,
            }
        )
    return {
        "provider": "okx",
        "symbol": symbol,
        "timeframe": timeframe.upper(),
        "count": len(candles),
        "candles": candles,
    }


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
        "bid": bids[0].get("price") if bids else None,
        "ask": asks[0].get("price") if asks else None,
        "closeout_bid": price.get("closeoutBid"),
        "closeout_ask": price.get("closeoutAsk"),
    }


def _oanda_candles(
    instrument: str,
    timeframe: str,
    limit: int,
) -> dict[str, Any]:
    headers, account_id = _oanda_headers()
    granularity = _OANDA_BARS.get(timeframe.upper())
    if not granularity:
        raise RuntimeError(f"unsupported OANDA timeframe: {timeframe}")
    limit = max(1, min(int(limit), _MAX_CANDLES))
    data = _get_json(
        (
            f"{_oanda_base_url()}/v3/accounts/{account_id}/instruments/"
            f"{instrument}/candles"
        ),
        params={
            "granularity": granularity,
            "count": limit,
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
                "o": mid.get("o"),
                "h": mid.get("h"),
                "l": mid.get("l"),
                "c": mid.get("c"),
                "v": row.get("volume"),
                "complete": row.get("complete"),
            }
        )
    return {
        "provider": "oanda",
        "instrument": instrument,
        "timeframe": timeframe.upper(),
        "count": len(candles),
        "candles": candles,
    }


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
        limit = int(arguments.get("limit") or 120)
        if not symbol:
            raise ValueError("symbol is required")
        if provider == "okx":
            return _okx_candles(symbol, timeframe, limit)
        if provider == "oanda":
            return _oanda_candles(symbol, timeframe, limit)
        raise ValueError("provider must be okx or oanda")

    if name == "market_orderbook":
        symbol = str(arguments.get("symbol") or "").strip()
        if not symbol:
            raise ValueError("symbol is required")
        return _okx_orderbook(symbol, int(arguments.get("depth") or 20))

    raise ValueError(f"unknown market tool: {name}")
