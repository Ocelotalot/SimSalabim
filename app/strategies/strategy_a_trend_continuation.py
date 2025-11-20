"""Strategy A — Trend-Continuation (TZ §4.7.1)."""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Symbol
from app.market.models import MarketState

from .base import BaseStrategy, Signal, TakeProfitLevel


class StrategyATrendContinuation(BaseStrategy):
    """Trend continuation around EMA20/50 alignment with VWAP filter."""

    id = StrategyId.STRATEGY_A
    name = "Trend-Continuation (EMA/VWAP)"

    def __init__(self, runtime_config):
        super().__init__(runtime_config)
        self.time_stop_bars = int(self.param("time_stop_bars", 20))
        self.ema_tolerance_ticks = float(self.param("ema20_tolerance_ticks", 1))
        self.tick_size = float(self.param("tick_size", 0.1))
        self.min_adx = float(self.param("min_adx", 20))
        self.min_rel_volume = float(self.param("min_rel_volume", 1.1))

    def _price_near_ema(self, price: float, ema_value: float | None) -> bool:
        if ema_value is None:
            return False
        return abs(price - ema_value) <= self.tick_size * self.ema_tolerance_ticks

    def _build_tp_levels(self, entry_price: float, sl_price: float, side: Side) -> tuple[TakeProfitLevel, ...]:
        risk = abs(entry_price - sl_price)
        if risk <= 0:
            return tuple()
        direction = 1 if side == Side.LONG else -1
        tp1 = entry_price + direction * risk
        tp2 = entry_price + direction * 2 * risk
        return (
            TakeProfitLevel(price=tp1, size_pct=0.5, label="tp1_1r"),
            TakeProfitLevel(price=tp2, size_pct=0.25, label="tp2_2r"),
        )

    def _build_signal(self, symbol: Symbol, side: Side, state: MarketState) -> Signal:
        entry_price = state.mid_price
        atr = state.ATR_14_5m
        risk_multiple = 1.2 * atr
        sl_price = entry_price - risk_multiple if side == Side.LONG else entry_price + risk_multiple
        tp_levels = self._build_tp_levels(entry_price, sl_price, side)
        return Signal(
            symbol=symbol,
            side=side,
            entry_type=EntryType.PULLBACK,
            strategy_id=self.id,
            entry_price=entry_price,
            target_risk_pct=self.param("target_risk_pct", 0.01),
            sl_price=sl_price,
            tp_levels=tp_levels,
            time_stop_bars=self.time_stop_bars,
            trailing_mode="ema_atr",
            trailing_params={
                "ema_period": 20,
                "atr_multiplier": 1.0,
            },
            metadata={"tf_profile": state.tf_profile.value},
        )

    def _market_allows_long(self, state: MarketState) -> bool:
        ema20 = getattr(state, "ema20", None)
        ema50 = getattr(state, "ema50", None)
        if not self._price_near_ema(state.mid_price, ema20):
            return False
        if ema20 is None or ema50 is None or ema20 <= ema50:
            return False
        if state.ADX_15m < self.min_adx:
            return False
        if state.rel_volume_5m < self.min_rel_volume:
            return False
        if state.vwap_slope < 0:
            return False
        if state.distance_to_vwap > 2 * state.sigma_vwap:
            return False
        return True

    def _market_allows_short(self, state: MarketState) -> bool:
        ema20 = getattr(state, "ema20", None)
        ema50 = getattr(state, "ema50", None)
        if not self._price_near_ema(state.mid_price, ema20):
            return False
        if ema20 is None or ema50 is None or ema20 >= ema50:
            return False
        if state.ADX_15m < self.min_adx:
            return False
        if state.rel_volume_5m < self.min_rel_volume:
            return False
        if state.vwap_slope > 0:
            return False
        if state.distance_to_vwap > 2 * state.sigma_vwap:
            return False
        return True

    def generate_signals(
        self,
        market_state: Mapping[Symbol, MarketState],
        position_state: Mapping[Symbol, object],
    ) -> list[Signal]:
        signals: list[Signal] = []
        for symbol, state in market_state.items():
            pos_side = self.position_side(position_state, symbol)
            if pos_side == Side.LONG:
                continue
            if self._market_allows_long(state):
                signals.append(self._build_signal(symbol, Side.LONG, state))
                continue
            if pos_side == Side.SHORT:
                continue
            if self._market_allows_short(state):
                signals.append(self._build_signal(symbol, Side.SHORT, state))
        self.logger.debug(
            "Strategy generated signals",
            extra={
                "strategy_id": self.id.value,
                "n_signals": len(signals),
                "symbols": sorted({str(s.symbol) for s in signals}),
            },
        )
        return signals
