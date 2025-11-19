from __future__ import annotations

from app.config.models import StrategyRuntimeConfig
from app.core.enums import Side
from app.core.types import Symbol
from app.strategies.strategy_e_liquidity_sweep import StrategyELiquiditySweep


def test_strategy_e_should_emit_short_on_liquidity_sweep(strategy_state_builder, market_state_factory) -> None:
    params = {"symbols": ["BTCUSDT"], "n_sweep": 10, "sl_buffer_ticks": 2, "tick_size": 0.5}
    config = StrategyRuntimeConfig(id=StrategyELiquiditySweep.id.value, enabled=True, priority=1, parameters=params)
    strategy = StrategyELiquiditySweep(config)
    base = market_state_factory(latency_ms=100, avg_slippage_bps=2.0, vwap_mean=100.0, sigma_vwap=1.5)
    state = strategy_state_builder(
        base,
        liquidity_absorption_pct=0.8,
        prev_delta_flow_1m=10_000,
        delta_flow_1m=-5_000,
        rolling_high_10=base.mid_price,
        rolling_low_10=base.mid_price - 5,
    )
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert len(signals) == 1
    assert signals[0].side is Side.SHORT
    assert signals[0].sl_price > signals[0].entry_price


def test_strategy_e_should_ignore_symbol_not_allowed(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyELiquiditySweep.id.value, enabled=True, priority=1, parameters={"symbols": ["ETHUSDT"]})
    strategy = StrategyELiquiditySweep(config)
    base = market_state_factory()
    state = strategy_state_builder(base, liquidity_absorption_pct=0.8, rolling_high_10=base.mid_price)
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert signals == []
