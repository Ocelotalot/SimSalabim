"""Strategy D — VWAP Mean-Reversion (TZ §4.7.4)."""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Symbol
from app.market.models import MarketState

from .base import BaseStrategy, Signal, TakeProfitLevel


class StrategyDVwapMeanReversion(BaseStrategy):
    """Fade VWAP extremes with absorption and delta-flow slowdown filters."""

    id = StrategyId.STRATEGY_D
    name = "VWAP Mean-Reversion"

    def __init__(self, runtime_config):
        super().__init__(runtime_config)
        self.k_upper = float(self.param("k_upper", 1.5))
        self.k_lower = float(self.param("k_lower", 1.5))
        self.time_stop_bars = int(self.param("time_stop_bars", 20))
        self.max_slope = float(self.param("max_vwap_slope", 0.0005))
        self.delta_flow_ratio = float(self.param("delta_flow_ratio", 0.7))
        self.absorption_threshold = float(self.param("absorption_threshold", 0.6))

    def _absorption_ok(self, state: MarketState) -> bool:
        absorption = getattr(state, "limit_absorption_pct", None)
        if absorption is None and hasattr(state, "orderflow_metrics"):
            metrics = getattr(state, "orderflow_metrics")
            absorption = metrics.get("limit_absorption_pct") if isinstance(metrics, dict) else None
        return absorption is not None and absorption >= self.absorption_threshold

    def _delta_flow_slowing(self, state: MarketState) -> bool:
        reference = getattr(state, "delta_flow_avg_5m", None)
        if reference is None:
            return True
        return abs(state.delta_flow_1m) <= self.delta_flow_ratio * abs(reference)

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
        for symbol, state in market_state.items():
            if abs(state.vwap_slope) > self.max_slope:
                continue
            if not self._absorption_ok(state):
                continue
            if not self._delta_flow_slowing(state):
                continue
            sigma = state.sigma_vwap
            if sigma <= 0:
                continue
            vwap = state.vwap_mean
            price = state.mid_price
            pos_side = self.position_side(position_state, symbol)
            upper_band = vwap + self.k_upper * sigma
            lower_band = vwap - self.k_lower * sigma
            atr = state.ATR_14_5m
            if price <= lower_band and pos_side != Side.LONG:
                sl_price = lower_band - 0.5 * atr
                signals.append(self._build_signal(symbol, Side.LONG, state, sl_price))
                continue
            if price >= upper_band and pos_side != Side.SHORT:
                sl_price = upper_band + 0.5 * atr
                signals.append(self._build_signal(symbol, Side.SHORT, state, sl_price))
        return signals
