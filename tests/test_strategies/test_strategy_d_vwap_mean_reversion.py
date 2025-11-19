from __future__ import annotations

import pytest

from app.config.models import StrategyRuntimeConfig
from app.core.enums import Side
from app.core.types import Symbol
from app.strategies.strategy_d_vwap_mean_reversion import StrategyDVwapMeanReversion


def test_strategy_d_should_emit_long_on_sweep_absorption(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyDVwapMeanReversion.id.value, enabled=True, priority=1)
    strategy = StrategyDVwapMeanReversion(config)
    base = market_state_factory(
        vwap_mean=100.0,
        sigma_vwap=2.0,
        ATR_14_5m=4.0,
        delta_flow_1m=5_000,
    )
    state = strategy_state_builder(
        base,
        limit_absorption_pct=0.8,
        delta_flow_avg_5m=10_000,
        mid_price=95.0,
    )
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert len(signals) == 1
    signal = signals[0]
    assert signal.side is Side.LONG
    assert signal.entry_type.value == "reversal"
    assert signal.tp_levels[0].price == pytest.approx(state.vwap_mean)


def test_strategy_d_should_skip_when_absorption_missing(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyDVwapMeanReversion.id.value, enabled=True, priority=1)
    strategy = StrategyDVwapMeanReversion(config)
    base = market_state_factory(vwap_mean=100.0, sigma_vwap=2.0, ATR_14_5m=4.0)
    state = strategy_state_builder(base, limit_absorption_pct=0.1, mid_price=105.0)
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert signals == []
