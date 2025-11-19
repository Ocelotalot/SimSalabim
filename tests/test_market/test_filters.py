from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config.models import SymbolGroup
from app.market.filters import PreTradeFilters, TradeStyle


@pytest.fixture
def filters(default_trading_config):
    return PreTradeFilters(
        default_trading_config.filters,
        slippage_auto_reject_factor=1.5,
        slippage_window_min=1,
        latency_max_ms=200,
    )


def test_filters_should_allow_core_liquidity(market_state_factory, filters) -> None:
    state = market_state_factory(
        group=SymbolGroup.CORE,
        depth_pm1_usd=5_000_000,
        spread_bps=2.0,
        rel_volume_5m=1.5,
        latency_ms=90,
    )
    allowed, reasons = filters.validate(state, trade_style=TradeStyle.TREND)
    assert allowed is True
    assert reasons == []


def test_filters_should_reject_on_low_liquidity(market_state_factory, filters) -> None:
    state = market_state_factory(
        group=SymbolGroup.PLUS,
        depth_pm1_usd=50_000,
        spread_bps=12.0,
        rel_volume_5m=1.2,
    )
    allowed, reasons = filters.validate(state, trade_style=TradeStyle.BREAKOUT)
    assert allowed is False
    assert "liquidity" in reasons


def test_filters_should_apply_rel_volume_threshold_per_style(market_state_factory, filters) -> None:
    state = market_state_factory(rel_volume_5m=0.9)
    allowed, reasons = filters.validate(state, trade_style=TradeStyle.MEAN_REVERSION)
    assert allowed is False
    assert "rel_volume" in reasons


def test_filters_should_reject_on_latency_and_slippage(market_state_factory, filters) -> None:
    ts = datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    state1 = market_state_factory(timestamp=ts, latency_ms=300, avg_slippage_bps=10.0, spread_bps=2.0)
    allowed1, reasons1 = filters.validate(state1, trade_style=TradeStyle.TREND)
    assert allowed1 is False
    assert "latency" in reasons1

    state2 = market_state_factory(
        timestamp=ts + timedelta(minutes=2),
        avg_slippage_bps=10.0,
        spread_bps=2.0,
    )
    allowed2, reasons2 = filters.validate(state2, trade_style=TradeStyle.TREND)
    assert allowed2 is False
    assert "slippage" in reasons2
