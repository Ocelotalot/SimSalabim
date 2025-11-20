"""Strategy C — Range-Break & Retest with ATR gate (TZ §4.7.3)."""
from __future__ import annotations

from typing import Mapping

from app.core.enums import EntryType, Regime, Side, StrategyId
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
            sigma = state.sigma_vwap
            if sigma <= 0:
                continue
            if state.atr_q_5m < self.atr_gate:
                continue
            if state.rel_volume_5m < self.rel_volume_gate:
                continue
            width = max(2 * sigma, state.mid_price * 0.002)
            pos_side = self.position_side(position_state, symbol)
            price = state.mid_price
            distance_from_mean = price - state.vwap_mean
            breakout_strength = distance_from_mean / max(sigma, 1e-9)
            in_range_regime = state.regime == Regime.RANGE

            if (
                breakout_strength >= 1.0
                and state.vwap_slope >= 0
                and (in_range_regime or breakout_strength <= 2.0)
                and pos_side != Side.LONG
            ):
                sl_price = price - width / 2
                signals.append(self._build_signal(symbol, Side.LONG, state, width, sl_price))
                continue
            if (
                breakout_strength <= -1.0
                and state.vwap_slope <= 0
                and (in_range_regime or breakout_strength >= -2.0)
                and pos_side != Side.SHORT
            ):
                sl_price = price + width / 2
                signals.append(self._build_signal(symbol, Side.SHORT, state, width, sl_price))
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
