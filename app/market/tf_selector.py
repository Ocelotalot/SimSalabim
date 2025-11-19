"""Dynamic TF profile selection per TZ §4.4."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict

from app.config.models import SymbolGroup
from app.core.enums import TfProfile
from app.core.types import Symbol


@dataclass(slots=True)
class TfSelectionMetrics:
    """Metrics referenced in TZ §4.4 when ranking AGGR/BAL/CONS."""

    atr_quantile: float
    rel_volume: float
    spread_bps: float
    depth_pm1_usd: float
    latency_ms: float
    avg_slippage_bps: float


@dataclass(slots=True)
class _TfProfileState:
    profile: TfProfile = TfProfile.BAL
    hits_aggr: int = 0
    hits_cons: int = 0
    cooldown_remaining: int = 0
    last_shift_date: date | None = None


class TfProfileSelector:
    """Implements AGGR/BAL/CONS transitions with hysteresis/cooldown (TZ §4.4)."""

    CORE_DEPTH = 3_000_000
    ALT_DEPTH = 1_000_000
    ATR_Q_AGGR_MIN = 0.7
    REL_VOLUME_AGGR_MIN = 1.4
    ATR_Q_CONS_MAX = 0.3
    REL_VOLUME_CONS_MAX = 0.8
    DEPTH_AGGR_MULT = 1.2
    SPREAD_AGGR_MAX_CORE = 2.5
    SPREAD_AGGR_MAX_ALT = 4.0
    LATENCY_AGGR_MAX = 120.0
    LATENCY_CONS_MIN = 160.0
    SLIPPAGE_CONS_FACTOR = 1.2

    def __init__(self, *, hits_up: int = 3, hits_down: int = 2, cooldown_bars: int = 5) -> None:
        self._hits_up = hits_up
        self._hits_down = hits_down
        self._cooldown_bars = cooldown_bars
        self._states: Dict[Symbol, _TfProfileState] = {}

    def current_profile(self, symbol: Symbol) -> TfProfile:
        state = self._states.get(symbol)
        return state.profile if state else TfProfile.BAL

    def update(
        self,
        *,
        symbol: Symbol,
        group: SymbolGroup,
        metrics: TfSelectionMetrics,
        timestamp: datetime,
    ) -> TfProfile:
        state = self._states.setdefault(symbol, _TfProfileState())
        self._apply_night_shift(state, timestamp)
        if state.cooldown_remaining > 0:
            state.cooldown_remaining -= 1
            return state.profile
        candidate = self._candidate(group, metrics)
        if candidate == TfProfile.AGGR:
            state.hits_aggr += 1
            state.hits_cons = 0
            if state.profile != TfProfile.AGGR and state.hits_aggr >= self._hits_up:
                state.profile = TfProfile.AGGR
                state.cooldown_remaining = self._cooldown_bars
                state.hits_aggr = 0
        elif candidate == TfProfile.CONS:
            state.hits_cons += 1
            state.hits_aggr = 0
            if state.profile != TfProfile.CONS and state.hits_cons >= self._hits_down:
                state.profile = TfProfile.CONS
                state.cooldown_remaining = self._cooldown_bars
                state.hits_cons = 0
        else:
            state.hits_aggr = 0
            state.hits_cons = 0
            if state.profile != TfProfile.BAL:
                state.profile = TfProfile.BAL
                state.cooldown_remaining = self._cooldown_bars
        return state.profile

    def _apply_night_shift(self, state: _TfProfileState, timestamp: datetime) -> None:
        if timestamp.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware for TF selection")
        local_date = timestamp.date()
        if timestamp.hour < 22 or state.last_shift_date == local_date:
            return
        if state.profile == TfProfile.AGGR:
            state.profile = TfProfile.BAL
        elif state.profile == TfProfile.BAL:
            state.profile = TfProfile.CONS
        state.cooldown_remaining = self._cooldown_bars
        state.hits_aggr = 0
        state.hits_cons = 0
        state.last_shift_date = local_date

    def _candidate(self, group: SymbolGroup, metrics: TfSelectionMetrics) -> TfProfile:
        base_depth = self.CORE_DEPTH if group == SymbolGroup.CORE else self.ALT_DEPTH
        spread_cap = self.SPREAD_AGGR_MAX_CORE if group == SymbolGroup.CORE else self.SPREAD_AGGR_MAX_ALT
        aggr_conditions = [
            metrics.atr_quantile >= self.ATR_Q_AGGR_MIN,
            metrics.rel_volume >= self.REL_VOLUME_AGGR_MIN,
            metrics.spread_bps <= spread_cap,
            metrics.depth_pm1_usd >= self.DEPTH_AGGR_MULT * base_depth,
            metrics.latency_ms <= self.LATENCY_AGGR_MAX,
            metrics.avg_slippage_bps <= max(metrics.spread_bps, 1e-9),
        ]
        if all(aggr_conditions):
            return TfProfile.AGGR
        cons_hits = 0
        if metrics.atr_quantile <= self.ATR_Q_CONS_MAX:
            cons_hits += 1
        if metrics.rel_volume <= self.REL_VOLUME_CONS_MAX:
            cons_hits += 1
        if metrics.latency_ms >= self.LATENCY_CONS_MIN or metrics.avg_slippage_bps >= self.SLIPPAGE_CONS_FACTOR * max(metrics.spread_bps, 1e-9):
            cons_hits += 1
        if cons_hits >= 2:
            return TfProfile.CONS
        return TfProfile.BAL


__all__ = ["TfProfileSelector", "TfSelectionMetrics"]
