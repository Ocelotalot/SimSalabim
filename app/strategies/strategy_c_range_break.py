"""Strategy C — Range-Break & Retest with ATR gate (TZ §4.7.3)."""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Symbol
from app.market.models import MarketState

from .base import BaseStrategy, Signal, TakeProfitLevel


class StrategyCRangeBreak(BaseStrategy):
    """Trades breakouts of 12-bar range with ATR/volume confirmation."""

    id = StrategyId.STRATEGY_C
    name = "Range Break & Retest"

    def __init__(self, runtime_config):
        super().__init__(runtime_config)
        self.atr_gate = float(self.param("atr_gate", 0.75))
        self.rel_volume_gate = float(self.param("rel_volume_gate", 1.3))
        self.retest_tolerance_bps = float(self.param("retest_tolerance_bps", 5))
        self.time_stop_bars = int(self.param("time_stop_bars", 20))

    def _range_levels(self, state: MarketState) -> tuple[float | None, float | None]:
        return getattr(state, "range_high_12", None), getattr(state, "range_low_12", None)

    def _within_retest(self, level: float, price: float) -> bool:
        if level <= 0:
            return False
        return abs(price - level) / level * 10_000 <= self.retest_tolerance_bps

    def _build_signal(self, symbol: Symbol, side: Side, state: MarketState, width: float, sl_price: float) -> Signal:
        entry_price = state.mid_price
        tp_price = entry_price + (width if side == Side.LONG else -width)
        tp_levels = (
            TakeProfitLevel(price=tp_price, size_pct=0.5, label="tp_range_height"),
        )
        return Signal(
            symbol=symbol,
            side=side,
            entry_type=EntryType.BREAKOUT,
            strategy_id=self.id,
            entry_price=entry_price,
            target_risk_pct=self.param("target_risk_pct", 0.01),
            sl_price=sl_price,
            tp_levels=tp_levels,
            time_stop_bars=self.time_stop_bars,
            trailing_mode="range_trail",
            trailing_params={"reentry_width": width / 2},
            metadata={"range_width": width},
        )

    def generate_signals(
        self,
        market_state: Mapping[Symbol, MarketState],
        position_state: Mapping[Symbol, object],
    ) -> list[Signal]:
        signals: list[Signal] = []
        for symbol, state in market_state.items():
            if state.atr_q_5m < self.atr_gate:
                continue
            if state.rel_volume_5m < self.rel_volume_gate:
                continue
            range_high, range_low = self._range_levels(state)
            if range_high is None or range_low is None:
                continue
            width = range_high - range_low
            if width <= 0:
                continue
            pos_side = self.position_side(position_state, symbol)
            price = state.mid_price
            if price >= range_high and pos_side != Side.LONG and self._within_retest(range_high, price):
                sl_price = range_high - width / 2
                signals.append(self._build_signal(symbol, Side.LONG, state, width, sl_price))
                continue
            if price <= range_low and pos_side != Side.SHORT and self._within_retest(range_low, price):
                sl_price = range_low + width / 2
                signals.append(self._build_signal(symbol, Side.SHORT, state, width, sl_price))
        self.logger.debug(
            "Strategy generated signals",
            extra={
                "strategy_id": self.id.value,
                "n_signals": len(signals),
                "symbols": sorted({str(s.symbol) for s in signals}),
            },
        )
        return signals
