"""Diagnostic strategy that always proposes a long entry when idle.

This strategy is intended purely for validating the signal pipeline. It emits a
single low-confidence long signal for BTCUSDT whenever there is no open position
for the symbol and a market state is available. Do not use in production.
"""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Symbol
from app.market.models import MarketState

from .base import BaseStrategy, Signal


class DebugAlwaysLongStrategy(BaseStrategy):
    id = StrategyId.STRATEGY_DEBUG_ALWAYS_LONG
    name = "Debug Always Long"

    def generate_signals(
        self,
        market_state: Mapping[Symbol, MarketState],
        position_state: Mapping[Symbol, object],
    ) -> list[Signal]:
        signals: list[Signal] = []
        symbol = Symbol("BTCUSDT")

        if self.position_side(position_state, symbol) is not None:
            return signals

        state = market_state.get(symbol)
        if state is None:
            return signals

        entry_price = float(state.mid_price)
        sl_price = entry_price * 0.99

        signals.append(
            Signal(
                symbol=symbol,
                side=Side.LONG,
                entry_type=EntryType.BREAKOUT,
                strategy_id=self.id,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_levels=(),
                metadata={"diagnostic": True},
            )
        )
        return signals
