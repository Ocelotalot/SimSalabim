"""Market regime detection with hysteresis (TZ §4.3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from app.core.enums import Regime
from app.core.types import Symbol


@dataclass(slots=True)
class _RegimeState:
    """Per-symbol storage for hysteresis (Trend/Range confirmation)."""

    regime: Regime = Regime.RANGE
    pending: Regime | None = None
    hits: int = 0


class RegimeClassifier:
    """Classify Trend/Range regimes per TZ §4.3 using ADX/ATR/VWAP."""

    def __init__(self, hysteresis_bars: int = 3) -> None:
        self._hysteresis_bars = hysteresis_bars
        self._states: Dict[Symbol, _RegimeState] = {}

    def update(self, *, symbol: Symbol, adx_15m: float, atr_quantile: float, vwap_slope: float) -> Regime:
        state = self._states.setdefault(symbol, _RegimeState())
        candidate = self._candidate(adx_15m, atr_quantile, vwap_slope)
        if candidate is None or candidate == state.regime:
            state.pending = None
            state.hits = 0
            return state.regime
        if state.pending == candidate:
            state.hits += 1
        else:
            state.pending = candidate
            state.hits = 1
        if state.hits >= self._hysteresis_bars:
            state.regime = candidate
            state.pending = None
            state.hits = 0
        return state.regime

    @staticmethod
    def _candidate(adx_15m: float, atr_quantile: float, vwap_slope: float) -> Regime | None:
        abs_slope = abs(vwap_slope)
        if adx_15m >= 20 and atr_quantile >= 0.6 and abs_slope > 0.0001:
            return Regime.TREND
        if adx_15m < 15 or atr_quantile <= 0.4 or abs_slope <= 0.0001:
            return Regime.RANGE
        return None

    def current(self, symbol: Symbol) -> Regime:
        """Return the last classified regime for ``symbol``."""

        state = self._states.get(symbol)
        return state.regime if state else Regime.RANGE


__all__ = ["RegimeClassifier"]
