"""Datamodels describing rotation scores and active symbol sets.

The rotation subsystem produces :class:`SymbolScore` objects for every symbol
with available market metrics. Each score bundles raw inputs (depth, spread,
relative volume, open-interest delta) with their normalized contributions and a
single weighted number per TZ §4.8. The :class:`RotationState` groups those
scores and publishes the whitelist of symbols that pre-trade filters and market
modules consult before allowing new entries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, Tuple


@dataclass(slots=True)
class SymbolScore:
    """Liquidity/performance snapshot for a tradable symbol.

    Attributes map one-to-one to the TZ §4.8 formula: raw metrics are stored for
    debugging, normalized factors expose how the final score was assembled, and
    ``score`` itself is the weighted sum (0.4 depth + 0.3 inverse spread + 0.2
    relative volume + 0.1 open-interest delta).
    """

    symbol: str
    updated_at: datetime
    depth_pm1_usd: float
    spread_bps: float
    rel_volume_5m: float
    oi_delta_5m: float
    normalized_depth: float
    normalized_inv_spread: float
    normalized_rel_volume: float
    normalized_oi_delta: float
    score: float

    def as_tuple(self) -> Tuple[str, float]:
        """Return a lightweight ``(symbol, score)`` tuple for sorting/selection."""

        return (self.symbol, self.score)


@dataclass(slots=True)
class RotationState:
    """Full rotation snapshot consumed by market filters.

    ``active_symbols`` acts as a whitelist for the rotation filter inside
    ``app.market.filters.PreTradeFilters``: strategies may propose entries only
    if their symbol is listed (plus any hard-enabled core symbols). The mapping
    of ``scores`` preserves detail for telemetry/inspection and is logged into
    JSON lines under ``logs/bot_YYYYMMDD.jsonl``.
    """

    timestamp: datetime
    min_score: float
    top_n: int
    check_interval_min: int
    scores: Dict[str, SymbolScore] = field(default_factory=dict)
    active_symbols: Tuple[str, ...] = field(default_factory=tuple)

    def is_symbol_allowed(self, symbol: str) -> bool:
        """Return ``True`` if ``symbol`` is eligible for new entries."""

        return symbol in self.active_symbols

    def top_scores(self, limit: int | None = None) -> Iterable[SymbolScore]:
        """Iterate over scores sorted in descending order (optionally capped)."""

        items = sorted(self.scores.values(), key=lambda score: score.score, reverse=True)
        if limit is None:
            return tuple(items)
        return tuple(items[:limit])


__all__ = ["RotationState", "SymbolScore"]
