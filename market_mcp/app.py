from __future__ import annotations

import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="AHS Market Data MCP", version="0.1.0")

OKX_BASE_URL = os.getenv("OKX_BASE_URL", "https://www.okx.com").rstrip("/")
OANDA_ENV = os.getenv("OANDA_ENV", "practice").strip().lower()
OANDA_BASE_URL = os.getenv(
    "OANDA_BASE_URL",
    "https://api-fxtrade.oanda.com" if OANDA_ENV == "live" else "https://api-fxpractice.oanda.com",
).rstrip("/")
OANDA_API_TOKEN = os.getenv("OANDA_API_TOKEN", "").strip()
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "").strip()
MCP_BEARER_TOKEN = os.getenv("MARKET_MCP_BEARER_TOKEN", "").strip()

HTTP_TIMEOUT = float(os.getenv("MARKET_HTTP_TIMEOUT_SECONDS", "15"))
MAX_CANDLES = 300
MAX_BOOK_DEPTH = 100

OKX_BARS = {
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

OANDA_GRANULARITIES = {
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


def _auth_ok(authorization: str | None) -> bool:
    if not MCP_BEARER_TOKEN:
        return True
    if not authorization:
        return False
    prefix = "Bearer "
    return authorization.startswith(prefix) and authorization[len(prefix):] == MCP_BEARER_TOKEN


def _require_auth(authorization: str | None) -> None:
    if not _auth_ok(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    import json

    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}],
        "isError": is_error,
    }


async def _get_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("provider returned a non-object response")
    return data


def _oanda_headers() -> dict[str, str]:
    if not OANDA_API_TOKEN:
        raise RuntimeError("OANDA_API_TOKEN is not configured")
    return {"Authorization": f"Bearer {OANDA_API_TOKEN}", "Accept-Datetime-Format": "RFC3339"}


def _require_oanda() -> None:
    if not OANDA_API_TOKEN or not OANDA_ACCOUNT_ID:
        raise RuntimeError("OANDA is not configured: set OANDA_API_TOKEN and OANDA_ACCOUNT_ID")


async def okx_snapshot(symbol: str) -> dict[str, Any]:
    data = await _get_json(f"{OKX_BASE_URL}/api/v5/market/ticker", params={"instId": symbol})
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"OKX returned no ticker for {symbol}")
    r = rows[0]
    return {
        "provider": "okx",
        "symbol": symbol,
        "timestamp_ms": int(r.get("ts") or 0),
        "last": r.get("last"),
        "bid": r.get("bidPx"),
        "ask": r.get("askPx"),
        "open_24h": r.get("open24h"),
        "high_24h": r.get("high24h"),
        "low_24h": r.get("low24h"),
        "volume_24h": r.get("vol24h"),
        "volume_quote_24h": r.get("volCcy24h"),
    }


async def okx_candles(symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
    bar = OKX_BARS.get(timeframe.upper())
    if not bar:
        raise RuntimeError(f"unsupported OKX timeframe: {timeframe}")
    limit = max(1, min(int(limit), MAX_CANDLES))
    data = await _get_json(
        f"{OKX_BASE_URL}/api/v5/market/candles",
        params={"instId": symbol, "bar": bar, "limit": limit},
    )
    rows = data.get("data") or []
    candles = []
    for r in reversed(rows):
        candles.append(
            {
                "t": int(r[0]),
                "o": r[1],
                "h": r[2],
                "l": r[3],
                "c": r[4],
                "v": r[5] if len(r) > 5 else None,
                "confirm": r[8] if len(r) > 8 else None,
            }
        )
    return {
        "provider": "okx",
        "symbol": symbol,
        "timeframe": timeframe.upper(),
        "count": len(candles),
        "candles": candles,
    }


async def okx_orderbook(symbol: str, depth: int) -> dict[str, Any]:
    depth = max(1, min(int(depth), MAX_BOOK_DEPTH))
    data = await _get_json(
        f"{OKX_BASE_URL}/api/v5/market/books",
        params={"instId": symbol, "sz": depth},
    )
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError(f"OKX returned no order book for {symbol}")
    r = rows[0]
    return {
        "provider": "okx",
        "symbol": symbol,
        "timestamp_ms": int(r.get("ts") or 0),
        "bids": r.get("bids") or [],
        "asks": r.get("asks") or [],
    }


async def oanda_snapshot(instrument: str) -> dict[str, Any]:
    _require_oanda()
    data = await _get_json(
        f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing",
        params={"instruments": instrument},
        headers=_oanda_headers(),
    )
    prices = data.get("prices") or []
    if not prices:
        raise RuntimeError(f"OANDA returned no price for {instrument}")
    p = prices[0]
    bids = p.get("bids") or []
    asks = p.get("asks") or []
    return {
        "provider": "oanda",
        "instrument": instrument,
        "time": p.get("time"),
        "status": p.get("status"),
        "bid": bids[0].get("price") if bids else None,
        "ask": asks[0].get("price") if asks else None,
        "closeout_bid": p.get("closeoutBid"),
        "closeout_ask": p.get("closeoutAsk"),
    }


async def oanda_candles(instrument: str, timeframe: str, limit: int) -> dict[str, Any]:
    _require_oanda()
    granularity = OANDA_GRANULARITIES.get(timeframe.upper())
    if not granularity:
        raise RuntimeError(f"unsupported OANDA timeframe: {timeframe}")
    limit = max(1, min(int(limit), MAX_CANDLES))
    data = await _get_json(
        f"{OANDA_BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/instruments/{instrument}/candles",
        params={"granularity": granularity, "count": limit, "price": "M"},
        headers=_oanda_headers(),
    )
    candles = []
    for r in data.get("candles") or []:
        mid = r.get("mid") or {}
        candles.append(
            {
                "t": r.get("time"),
                "o": mid.get("o"),
                "h": mid.get("h"),
                "l": mid.get("l"),
                "c": mid.get("c"),
                "v": r.get("volume"),
                "complete": r.get("complete"),
            }
        )
    return {
        "provider": "oanda",
        "instrument": instrument,
        "timeframe": timeframe.upper(),
        "count": len(candles),
        "candles": candles,
    }


TOOLS = [
    {
        "name": "market_snapshot",
        "description": "Get a compact live market snapshot. Use OKX for crypto and OANDA for FX/metals such as XAU_USD. Read-only.",
        "inputSchema": {
            "type": "object",
            "required": ["provider", "symbol"],
            "properties": {
                "provider": {"type": "string", "enum": ["okx", "oanda"]},
                "symbol": {"type": "string", "description": "OKX example BTC-USDT; OANDA example XAU_USD or EUR_USD."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "market_candles",
        "description": "Get OHLC/volume chart candles for technical analysis. Read-only and bounded to 300 candles.",
        "inputSchema": {
            "type": "object",
            "required": ["provider", "symbol", "timeframe"],
            "properties": {
                "provider": {"type": "string", "enum": ["okx", "oanda"]},
                "symbol": {"type": "string"},
                "timeframe": {"type": "string", "description": "Common values: M1, M5, M15, M30, H1, H4, D1, W1."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 300, "default": 120},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "market_orderbook",
        "description": "Get OKX public order-book depth for crypto. Read-only. OANDA is intentionally not exposed here.",
        "inputSchema": {
            "type": "object",
            "required": ["symbol"],
            "properties": {
                "symbol": {"type": "string", "description": "Example: BTC-USDT."},
                "depth": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
    },
]


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "ahs-market-data-mcp",
        "okx": True,
        "oanda_configured": bool(OANDA_API_TOKEN and OANDA_ACCOUNT_ID),
        "auth_enabled": bool(MCP_BEARER_TOKEN),
        "time": int(time.time()),
    }


@app.get("/mcp")
async def mcp_get(authorization: str | None = Header(default=None)) -> Response:
    _require_auth(authorization)
    return JSONResponse({"status": "ok", "transport": "streamable-http", "endpoint": "/mcp"})


@app.post("/mcp")
async def mcp_post(request: Request, authorization: str | None = Header(default=None)) -> Response:
    _require_auth(authorization)
    payload = await request.json()
    method = payload.get("method")
    request_id = payload.get("id")

    if method == "notifications/initialized":
        return Response(status_code=202)

    if method == "initialize":
        result = {
            "protocolVersion": payload.get("params", {}).get("protocolVersion") or "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ahs-market-data-mcp", "version": "0.1.0"},
        }
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})

    if method == "tools/call":
        params = payload.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            if name == "market_snapshot":
                provider = str(args.get("provider") or "").lower()
                symbol = str(args.get("symbol") or "").strip()
                if provider == "okx":
                    result = await okx_snapshot(symbol)
                elif provider == "oanda":
                    result = await oanda_snapshot(symbol)
                else:
                    raise RuntimeError("provider must be okx or oanda")
            elif name == "market_candles":
                provider = str(args.get("provider") or "").lower()
                symbol = str(args.get("symbol") or "").strip()
                timeframe = str(args.get("timeframe") or "M1")
                limit = int(args.get("limit") or 120)
                if provider == "okx":
                    result = await okx_candles(symbol, timeframe, limit)
                elif provider == "oanda":
                    result = await oanda_candles(symbol, timeframe, limit)
                else:
                    raise RuntimeError("provider must be okx or oanda")
            elif name == "market_orderbook":
                symbol = str(args.get("symbol") or "").strip()
                depth = int(args.get("depth") or 20)
                result = await okx_orderbook(symbol, depth)
            else:
                raise RuntimeError(f"unknown tool: {name}")
            tool_result = _text_result(result)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            tool_result = _text_result({"error": type(exc).__name__, "message": str(exc)}, is_error=True)

        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": tool_result})

    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}},
        status_code=200,
    )
