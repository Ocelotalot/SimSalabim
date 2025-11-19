from __future__ import annotations

from app.config.models import StrategyRuntimeConfig
from app.core.enums import Side
from app.core.types import Symbol
from app.strategies.strategy_a_trend_continuation import StrategyATrendContinuation


def test_strategy_a_should_emit_long_signal_when_ema_alignment(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyATrendContinuation.id.value, enabled=True, priority=1)
    strategy = StrategyATrendContinuation(config)
    base = market_state_factory(ATR_14_5m=6.0, rel_volume_5m=1.5, ADX_15m=30, vwap_slope=0.001, sigma_vwap=1.0, distance_to_vwap=0.3)
    state = strategy_state_builder(base, ema20=base.mid_price + 0.05, ema50=base.mid_price - 0.5)
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert len(signals) == 1
    signal = signals[0]
    assert signal.side is Side.LONG
    assert signal.entry_type.value == "pullback"
    assert signal.sl_price < signal.entry_price
    assert len(signal.tp_levels) == 2
    assert signal.time_stop_bars == strategy.time_stop_bars


def test_strategy_a_should_block_when_adx_low(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyATrendContinuation.id.value, enabled=True, priority=1)
    strategy = StrategyATrendContinuation(config)
    base = market_state_factory(ATR_14_5m=6.0, rel_volume_5m=1.5, ADX_15m=10)
    state = strategy_state_builder(base, ema20=base.mid_price + 0.05, ema50=base.mid_price - 0.5)
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert signals == []
