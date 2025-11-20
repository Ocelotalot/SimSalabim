"""Strategy D — VWAP Mean-Reversion (TZ §4.7.4)."""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Symbol
from app.market.models import MarketState

from .base import BaseStrategy, Signal, TakeProfitLevel


class StrategyDVwapMeanReversion(BaseStrategy):
    """Fade VWAP extremes with flow/volume brakes when slope is muted."""

    id = StrategyId.STRATEGY_D
    name = "VWAP Mean-Reversion"

    def __init__(self, runtime_config):
        super().__init__(runtime_config)
        self.k_upper = float(self.param("k_upper", 1.5))
        self.k_lower = float(self.param("k_lower", 1.5))
        self.time_stop_bars = int(self.param("time_stop_bars", 20))
        self.max_slope = float(self.param("max_vwap_slope", 0.0005))
        self.min_rel_volume = float(self.param("min_rel_volume", 0.9))

    def _build_tp_levels(self, symbol: Symbol, side: Side, state: MarketState) -> tuple[TakeProfitLevel, ...]:
        vwap = state.vwap_mean
        sigma = state.sigma_vwap
        tp1 = vwap
        target_mode = self.param("tp2_mode", "opposite_band")
        if target_mode == "mid":
            direction = 1 if side == Side.LONG else -1
            tp2 = vwap + direction * 0.5 * sigma
        elif side == Side.LONG:
            tp2 = vwap + self.k_upper * sigma
        else:
            tp2 = vwap - self.k_lower * sigma
        return (
            TakeProfitLevel(price=tp1, size_pct=0.5, label="tp1_vwap"),
            TakeProfitLevel(price=tp2, size_pct=0.25, label="tp2_band"),
        )

    def _build_signal(self, symbol: Symbol, side: Side, state: MarketState, sl_price: float) -> Signal:
        tp_levels = self._build_tp_levels(symbol, side, state)
        return Signal(
            symbol=symbol,
            side=side,
            entry_type=EntryType.REVERSAL,
            strategy_id=self.id,
            entry_price=state.mid_price,
            target_risk_pct=self.param("target_risk_pct", 0.01),
            sl_price=sl_price,
            tp_levels=tp_levels,
            time_stop_bars=self.time_stop_bars,
            trailing_mode="vwap_band",
            trailing_params={"sigma_multiplier": 1.0},
            metadata={"vwap_mean": state.vwap_mean},
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
            if abs(state.vwap_slope) > self.max_slope:
                continue
            if state.rel_volume_5m < self.min_rel_volume:
                continue
            sigma = state.sigma_vwap
            if sigma <= 0:
                continue
            vwap = state.vwap_mean
            price = state.mid_price
            pos_side = self.position_side(position_state, symbol)
            upper_band = vwap + self.k_upper * sigma
            lower_band = vwap - self.k_lower * sigma
            atr = max(state.ATR_14_5m, price * 0.001)
            distance = price - vwap
            flow_bias = state.delta_flow_1m
            oi_bias = state.oi_delta_5m

            if (
                distance <= -self.k_lower * sigma
                and pos_side != Side.LONG
                and (flow_bias >= 0 or oi_bias >= 0)
            ):
                sl_price = lower_band - 0.5 * atr
                signals.append(self._build_signal(symbol, Side.LONG, state, sl_price))
                continue
            if (
                distance >= self.k_upper * sigma
                and pos_side != Side.SHORT
                and (flow_bias <= 0 or oi_bias <= 0)
            ):
                sl_price = upper_band + 0.5 * atr
                signals.append(self._build_signal(symbol, Side.SHORT, state, sl_price))
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
