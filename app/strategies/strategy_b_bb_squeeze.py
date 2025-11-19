"""Strategy B — BB-Squeeze → Break & Retest (TZ §4.7.2)."""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Symbol
from app.market.models import MarketState

from .base import BaseStrategy, Signal, TakeProfitLevel


class StrategyBBBBSqueeze(BaseStrategy):
    """Detects Bollinger squeeze, waits for breakout and retest before entry."""

    id = StrategyId.STRATEGY_B
    name = "BB-Squeeze Break & Retest"

    def __init__(self, runtime_config):
        super().__init__(runtime_config)
        self.tolerance_bps = float(self.param("retest_tolerance_bps", 5))
        self.min_rel_volume = float(self.param("min_rel_volume", 1.3))
        self.min_squeeze_bars = int(self.param("min_squeeze_bars", 20))
        self.time_stop_bars = int(self.param("time_stop_bars", 20))

    def _build_tp_levels(self, entry_price: float, atr: float, side: Side) -> tuple[TakeProfitLevel, ...]:
        direction = 1 if side == Side.LONG else -1
        tp1 = entry_price + direction * 1.5 * atr
        tp2 = entry_price + direction * 3.0 * atr
        return (
            TakeProfitLevel(price=tp1, size_pct=0.5, label="tp1_1.5atr"),
            TakeProfitLevel(price=tp2, size_pct=0.25, label="tp2_3atr"),
        )

    def _build_signal(self, symbol: Symbol, side: Side, state: MarketState, atr: float, sl_price: float) -> Signal:
        entry_price = state.mid_price
        tp_levels = self._build_tp_levels(entry_price, atr, side)
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
            trailing_mode="swing_trail",
            trailing_params={"atr_multiplier": 1.0},
            metadata={"pattern": "bb_squeeze"},
        )

    def _within_retest(self, level: float, price: float) -> bool:
        if level <= 0:
            return False
        delta_bps = abs(price - level) / level * 10_000
        return delta_bps <= self.tolerance_bps

    def _can_long(self, state: MarketState) -> bool:
        bb_upper = getattr(state, "bb_upper", None)
        kc_upper = getattr(state, "kc_upper", None)
        squeeze_bars = getattr(state, "bb_inside_kc_bars", 0)
        if squeeze_bars < self.min_squeeze_bars:
            return False
        if bb_upper is None or kc_upper is None:
            return False
        if getattr(state, "close", state.mid_price) <= bb_upper:
            return False
        if state.rel_volume_5m < self.min_rel_volume:
            return False
        if state.oi_delta_5m <= 0:
            return False
        return True

    def _can_short(self, state: MarketState) -> bool:
        bb_lower = getattr(state, "bb_lower", None)
        kc_lower = getattr(state, "kc_lower", None)
        squeeze_bars = getattr(state, "bb_inside_kc_bars", 0)
        if squeeze_bars < self.min_squeeze_bars:
            return False
        if bb_lower is None or kc_lower is None:
            return False
        if getattr(state, "close", state.mid_price) >= bb_lower:
            return False
        if state.rel_volume_5m < self.min_rel_volume:
            return False
        if state.oi_delta_5m >= 0:
            return False
        return True

    def generate_signals(
        self,
        market_state: Mapping[Symbol, MarketState],
        position_state: Mapping[Symbol, object],
    ) -> list[Signal]:
        signals: list[Signal] = []
        atr_mult = float(self.param("atr_stop_multiplier", 1.0))
        for symbol, state in market_state.items():
            pos_side = self.position_side(position_state, symbol)
            atr = state.ATR_14_5m
            if self._can_long(state) and pos_side != Side.LONG:
                upper_break = getattr(state, "bb_upper", state.mid_price)
                if not self._within_retest(upper_break, state.mid_price):
                    continue
                lower_bb = getattr(state, "bb_lower", state.mid_price)
                sl_price = lower_bb - atr_mult * atr
                signals.append(self._build_signal(symbol, Side.LONG, state, atr, sl_price))
                continue
            if self._can_short(state) and pos_side != Side.SHORT:
                lower_break = getattr(state, "bb_lower", state.mid_price)
                if not self._within_retest(lower_break, state.mid_price):
                    continue
                upper_bb = getattr(state, "bb_upper", state.mid_price)
                sl_price = upper_bb + atr_mult * atr
                signals.append(self._build_signal(symbol, Side.SHORT, state, atr, sl_price))
        return signals
