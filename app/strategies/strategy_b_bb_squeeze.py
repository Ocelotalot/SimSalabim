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
        self.min_rel_volume = float(self.param("min_rel_volume", 1.05))
        self.max_atr_quantile = float(self.param("max_atr_quantile_for_squeeze", 0.65))
        self.breakout_sigma = float(self.param("breakout_sigma", 1.0))
        self.retest_sigma = float(self.param("retest_sigma", 0.6))
        self.time_stop_bars = int(self.param("time_stop_bars", 20))
        self.atr_stop_multiplier = float(self.param("atr_stop_multiplier", 1.2))

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
            sigma = state.sigma_vwap
            if sigma <= 0:
                continue
            if state.rel_volume_5m < self.min_rel_volume:
                continue
            if state.atr_q_5m > self.max_atr_quantile:
                continue
            atr = max(state.ATR_14_5m, state.mid_price * 0.001)
            breakout_strength = (state.mid_price - state.vwap_mean) / max(sigma, 1e-9)
            flow_bias = state.delta_flow_1m
            slope = state.vwap_slope

            if (
                breakout_strength >= self.breakout_sigma
                and (slope >= 0 or flow_bias > 0)
                and breakout_strength <= self.breakout_sigma + self.retest_sigma
                and pos_side != Side.LONG
            ):
                sl_price = state.mid_price - self.atr_stop_multiplier * atr
                signals.append(self._build_signal(symbol, Side.LONG, state, atr, sl_price))
                continue
            if (
                breakout_strength <= -self.breakout_sigma
                and (slope <= 0 or flow_bias < 0)
                and breakout_strength >= -self.breakout_sigma - self.retest_sigma
                and pos_side != Side.SHORT
            ):
                sl_price = state.mid_price + self.atr_stop_multiplier * atr
                signals.append(self._build_signal(symbol, Side.SHORT, state, atr, sl_price))
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
