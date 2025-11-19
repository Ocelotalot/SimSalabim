from __future__ import annotations

from app.config.models import StrategyRuntimeConfig
from app.core.enums import Side
from app.core.types import Symbol
from app.strategies.strategy_b_bb_squeeze import StrategyBBBBSqueeze


def test_strategy_b_should_emit_breakout_signal(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyBBBBSqueeze.id.value, enabled=True, priority=1)
    strategy = StrategyBBBBSqueeze(config)
    base = market_state_factory(rel_volume_5m=1.5, ATR_14_5m=4.0, oi_delta_5m=15_000)
    state = strategy_state_builder(
        base,
        bb_upper=base.mid_price - 0.005,
        bb_lower=base.mid_price - 2.0,
        kc_upper=base.mid_price,
        kc_lower=base.mid_price - 1.0,
        bb_inside_kc_bars=25,
        close=base.mid_price + 0.2,
    )
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert len(signals) == 1
    signal = signals[0]
    assert signal.side is Side.LONG
    assert signal.entry_type.value == "breakout"
    assert signal.sl_price < signal.entry_price
    assert signal.tp_levels[0].price > signal.entry_price


def test_strategy_b_should_skip_without_retest(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyBBBBSqueeze.id.value, enabled=True, priority=1)
    strategy = StrategyBBBBSqueeze(config)
    base = market_state_factory(rel_volume_5m=1.5, ATR_14_5m=4.0, oi_delta_5m=-10_000)
    state = strategy_state_builder(
        base,
        bb_lower=base.mid_price - 5.0,
        kc_lower=base.mid_price + 0.5,
        bb_inside_kc_bars=30,
        close=base.mid_price,
    )
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert signals == []
