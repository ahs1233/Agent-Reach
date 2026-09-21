from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

PANWATCH_ROOT = Path(__file__).resolve().parents[1] / "integrations" / "panwatch"
sys.path.insert(0, str(PANWATCH_ROOT))

from src.modules.strategy.xau_intraday import XAUIntradayEngine  # noqa: E402
from src.platform.marketdata.xau_models import (  # noqa: E402
    XAUBar,
    XAUQuote,
    XAUTimeframe,
)


def _bullish_bars(
    timeframe: XAUTimeframe,
    now: datetime,
    *,
    execution_eligible: bool = True,
) -> list[XAUBar]:
    spacing = {
        XAUTimeframe.M1: timedelta(minutes=1),
        XAUTimeframe.M5: timedelta(minutes=5),
        XAUTimeframe.M15: timedelta(minutes=15),
    }[timeframe]
    bars = []
    for i in range(30):
        price = 4200.0 + i * 1.5
        bars.append(
            XAUBar(
                timestamp=now - spacing * (29 - i),
                timeframe=timeframe,
                open=price,
                high=price + 1.2,
                low=price - 0.3,
                close=price + 1.0,
                volume=100 + i,
                source="test",
                execution_eligible=execution_eligible,
            )
        )
    return bars


def _all_frames(now: datetime, *, execution_eligible: bool = True):
    return {
        timeframe: _bullish_bars(
            timeframe,
            now,
            execution_eligible=execution_eligible,
        )
        for timeframe in (XAUTimeframe.M1, XAUTimeframe.M5, XAUTimeframe.M15)
    }


def test_macro_conflict_is_warning_not_execution_block():
    now = datetime.now(UTC)
    quote = XAUQuote(
        bid=4244.0,
        ask=4244.3,
        observed_at=now,
        source="spot-test",
        execution_eligible=True,
    )
    result = XAUIntradayEngine(require_execution_data=True).analyze(
        _all_frames(now),
        quote=quote,
        macro_bias=-1,
        now=now,
    )

    assert result.blocked is False
    assert result.candidate == "long_setup"
    assert "macro_bias_conflicts_long" in result.warnings
    assert "macro_bias_conflicts_long" not in result.block_reasons


def test_execution_mode_blocks_research_proxy_bars_and_missing_spot_quote():
    now = datetime.now(UTC)
    result = XAUIntradayEngine(require_execution_data=True).analyze(
        _all_frames(now, execution_eligible=False),
        quote=None,
        now=now,
    )

    assert result.blocked is True
    assert result.candidate == "none"
    assert "execution_quote_missing" in result.block_reasons
    assert "execution_1m_bars_required" in result.block_reasons
    assert "execution_5m_bars_required" in result.block_reasons
    assert "execution_15m_bars_required" in result.block_reasons


def test_high_impact_event_gate_blocks_otherwise_valid_setup():
    now = datetime.now(UTC)
    quote = XAUQuote(
        bid=4244.0,
        ask=4244.2,
        observed_at=now,
        source="spot-test",
        execution_eligible=True,
    )
    result = XAUIntradayEngine(require_execution_data=True).analyze(
        _all_frames(now),
        quote=quote,
        event_risk=True,
        now=now,
    )

    assert result.blocked is True
    assert result.candidate == "none"
    assert "high_impact_event_gate" in result.block_reasons
