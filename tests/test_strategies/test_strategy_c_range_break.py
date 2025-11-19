from __future__ import annotations

import pytest

from app.config.models import StrategyRuntimeConfig
from app.core.enums import Side
from app.core.types import Symbol
from app.strategies.strategy_c_range_break import StrategyCRangeBreak


def test_strategy_c_should_emit_long_on_break_and_retest(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyCRangeBreak.id.value, enabled=True, priority=1)
    strategy = StrategyCRangeBreak(config)
    base = market_state_factory(atr_q_5m=0.9, rel_volume_5m=1.5)
    high = base.mid_price
    low = base.mid_price - 3
    state = strategy_state_builder(base, range_high_12=high, range_low_12=low)
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert len(signals) == 1
    assert signals[0].side is Side.LONG
    assert signals[0].tp_levels[0].price == pytest.approx(state.mid_price + (high - low))


def test_strategy_c_should_skip_when_range_invalid(strategy_state_builder, market_state_factory) -> None:
    config = StrategyRuntimeConfig(id=StrategyCRangeBreak.id.value, enabled=True, priority=1)
    strategy = StrategyCRangeBreak(config)
    base = market_state_factory(atr_q_5m=0.9, rel_volume_5m=1.5)
    state = strategy_state_builder(base, range_high_12=base.mid_price, range_low_12=base.mid_price)
    signals = strategy.generate_signals({Symbol("BTCUSDT"): state}, {})
    assert signals == []
