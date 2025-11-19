"""Rotation scoring engine per TZ §4.8.

``RotationEngine`` ingests latest :class:`~app.market.models.MarketState`
objects, normalizes the metrics referenced by the rotation formula, and emits a
:class:`RotationState` snapshot. The resulting whitelist is consumed by
``market.filters`` and downstream risk logic to block new entries when liquidity
or execution quality deteriorate.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Mapping, Sequence

from app.config.models import RotationConfig, SymbolConfig, SymbolGroup
from app.core.errors import RotationError
from app.market.models import MarketState
from app.rotation.models import RotationState, SymbolScore


@dataclass(slots=True)
class _RollingMetric:
    """Maintain a sliding window of numeric observations for normalization."""

    window: timedelta
    samples: Deque[tuple[datetime, float]]

    def __init__(self, window: timedelta) -> None:
        self.window = window
        self.samples = deque()

    def add(self, timestamp: datetime, value: float) -> None:
        self.samples.append((timestamp, value))
        self._trim(timestamp)

    def normalize(self, value: float) -> float:
        if not self.samples:
            return 0.0
        values = [sample for _, sample in self.samples]
        min_value = min(values)
        max_value = max(values)
        if max_value - min_value < 1e-9:
            return 1.0
        return (value - min_value) / (max_value - min_value)

    def _trim(self, now: datetime) -> None:
        cutoff = now - self.window
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()


class RotationEngine:
    """Compute rotation scores and select active symbols."""

    def __init__(
        self,
        config: RotationConfig,
        symbols: Sequence[SymbolConfig],
        *,
        normalization_window_min: int = 24 * 60,
    ) -> None:
        self._config = config
        self._symbol_map: Dict[str, SymbolConfig] = {cfg.symbol: cfg for cfg in symbols}
        window = timedelta(minutes=normalization_window_min)
        self._depth_metric = _RollingMetric(window)
        self._spread_metric = _RollingMetric(window)
        self._rel_volume_metric = _RollingMetric(window)
        self._oi_delta_metric = _RollingMetric(window)
        self._state: RotationState | None = None

    @property
    def state(self) -> RotationState | None:
        return self._state

    def update(
        self,
        market_states: Mapping[str, MarketState],
        *,
        now: datetime | None = None,
    ) -> RotationState:
        """Recalculate the rotation state if interval and config permit."""

        timestamp = now or datetime.now(tz=timezone.utc)
        if not self._config.enabled:
            active = self._enabled_symbols()
            self._state = RotationState(
                timestamp=timestamp,
                min_score=self._config.min_score_for_new_entry,
                top_n=self._config.max_active_symbols,
                check_interval_min=self._config.check_interval_min,
                scores={},
                active_symbols=tuple(active),
            )
            return self._state

        if self._state is not None:
            delta = timestamp - self._state.timestamp
            if delta < timedelta(minutes=self._config.check_interval_min):
                return self._state

        if not market_states:
            raise RotationError("rotation_engine.update() requires market states")

        scores = self._compute_scores(market_states, timestamp)
        active_symbols = self._select_active_symbols(scores)
        self._state = RotationState(
            timestamp=timestamp,
            min_score=self._config.min_score_for_new_entry,
            top_n=self._config.max_active_symbols,
            check_interval_min=self._config.check_interval_min,
            scores=scores,
            active_symbols=tuple(active_symbols),
        )
        return self._state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _compute_scores(
        self,
        market_states: Mapping[str, MarketState],
        timestamp: datetime,
    ) -> Dict[str, SymbolScore]:
        scores: Dict[str, SymbolScore] = {}
        for symbol, state in market_states.items():
            config = self._symbol_map.get(symbol)
            if not config or config.enabled is False:
                continue
            depth = max(state.depth_pm1_usd, 0.0)
            spread_inv = 1.0 / max(state.spread_bps, 1e-6)
            rel_volume = max(state.rel_volume_5m, 0.0)
            oi_delta = state.oi_delta_5m

            self._depth_metric.add(timestamp, depth)
            self._spread_metric.add(timestamp, spread_inv)
            self._rel_volume_metric.add(timestamp, rel_volume)
            self._oi_delta_metric.add(timestamp, oi_delta)

            normalized_depth = self._depth_metric.normalize(depth)
            normalized_inv_spread = self._spread_metric.normalize(spread_inv)
            normalized_rel_volume = self._rel_volume_metric.normalize(rel_volume)
            normalized_oi_delta = self._oi_delta_metric.normalize(oi_delta)

            score = (
                0.4 * normalized_depth
                + 0.3 * normalized_inv_spread
                + 0.2 * normalized_rel_volume
                + 0.1 * normalized_oi_delta
            )
            scores[symbol] = SymbolScore(
                symbol=symbol,
                updated_at=timestamp,
                depth_pm1_usd=depth,
                spread_bps=state.spread_bps,
                rel_volume_5m=state.rel_volume_5m,
                oi_delta_5m=state.oi_delta_5m,
                normalized_depth=normalized_depth,
                normalized_inv_spread=normalized_inv_spread,
                normalized_rel_volume=normalized_rel_volume,
                normalized_oi_delta=normalized_oi_delta,
                score=score,
            )
        if not scores:
            raise RotationError("No eligible symbols produced a rotation score")
        return scores

    def _enabled_symbols(self) -> Sequence[str]:
        return [cfg.symbol for cfg in self._symbol_map.values() if cfg.enabled]

    def _select_active_symbols(self, scores: Mapping[str, SymbolScore]) -> Sequence[str]:
        min_score = self._config.min_score_for_new_entry
        top_n = self._config.max_active_symbols
        core_symbols = [
            cfg.symbol
            for cfg in self._symbol_map.values()
            if cfg.enabled and cfg.group == SymbolGroup.CORE
        ]
        eligible_scores = [score for symbol, score in scores.items() if symbol in self._symbol_map]
        eligible_scores.sort(key=lambda score: score.score, reverse=True)
        high_quality = [score for score in eligible_scores if score.score >= min_score]

        active: list[str] = []
        seen = set()
        for symbol in core_symbols:
            if symbol not in seen:
                active.append(symbol)
                seen.add(symbol)

        for score in high_quality:
            if score.symbol not in seen:
                active.append(score.symbol)
                seen.add(score.symbol)

        if len(active) < top_n:
            for score in eligible_scores:
                if score.symbol in seen:
                    continue
                active.append(score.symbol)
                seen.add(score.symbol)
                if len(active) >= top_n:
                    break

        return active


__all__ = ["RotationEngine"]
