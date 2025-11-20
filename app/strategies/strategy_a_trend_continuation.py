"""Strategy A — Trend-Continuation (TZ §4.7.1)."""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Symbol
from app.market.models import MarketState

from .base import BaseStrategy, Signal, TakeProfitLevel


class StrategyATrendContinuation(BaseStrategy):
    """Trend continuation around VWAP slope/ADX alignment with controlled pullbacks."""

    id = StrategyId.STRATEGY_A
    name = "Trend-Continuation (EMA/VWAP)"

    def __init__(self, runtime_config):
        super().__init__(runtime_config)
        self.time_stop_bars = int(self.param("time_stop_bars", 20))
        self.min_adx = float(self.param("min_adx", 18))
        self.min_rel_volume = float(self.param("min_rel_volume", 1.0))
        self.min_vwap_slope = float(self.param("min_vwap_slope", 0.0))
        self.max_distance_sigma = float(self.param("max_distance_sigma", 2.0))

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
        atr = max(state.ATR_14_5m, entry_price * 0.001)
        risk_multiple = max(1.2 * atr, entry_price * 0.001)
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
        sigma = state.sigma_vwap
        if sigma <= 0:
            return False
        if state.ADX_15m < self.min_adx:
            return False
        if state.rel_volume_5m < self.min_rel_volume:
            return False
        if state.vwap_slope <= self.min_vwap_slope:
            return False
        if state.mid_price < state.vwap_mean:
            return False
        if state.distance_to_vwap > self.max_distance_sigma * sigma:
            return False
        return True

    def _market_allows_short(self, state: MarketState) -> bool:
        sigma = state.sigma_vwap
        if sigma <= 0:
            return False
        if state.ADX_15m < self.min_adx:
            return False
        if state.rel_volume_5m < self.min_rel_volume:
            return False
        if state.vwap_slope >= -self.min_vwap_slope:
            return False
        if state.mid_price > state.vwap_mean:
            return False
        if state.distance_to_vwap > self.max_distance_sigma * sigma:
            return False
        return True

    def generate_signals(
        self,
        market_state: Mapping[Symbol, MarketState],
        position_state: Mapping[Symbol, object],
    ) -> list[Signal]:
        signals: list[Signal] = []
        symbols = sorted(str(sym) for sym in market_state.keys())
        tf_profiles = sorted({state.tf_profile.value for state in market_state.values()})
        self.logger.debug(
            "generate_signals called",
            extra={
                "strategy_id": self.id.value,
                "strategy_name": self.name,
                "symbols": symbols,
                "tf_profiles": tf_profiles,
            },
        )
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
        symbols_with_signals = sorted({str(s.symbol) for s in signals})
        self.logger.debug(
            "Strategy generated signals",
            extra={
                "strategy_id": self.id.value,
                "n_signals": len(signals),
                "symbols": symbols_with_signals,
            },
        )
        return signals
