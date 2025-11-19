"""Pre-trade filters enforcing TZ §3.3 and §4.5 requirements."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict

from app.config.models import FiltersConfig, SymbolGroup
from app.market.models import MarketState


class TradeStyle(str, Enum):
    """Strategy-style hints controlling rel_volume thresholds (TZ §4.5)."""

    TREND = "trend"
    BREAKOUT = "breakout"
    MEAN_REVERSION = "mean_reversion"


@dataclass(frozen=True)
class _LiquidityThreshold:
    min_depth_usd: float
    max_spread_bps: float


class PreTradeFilters:
    """Group/liquidity/latency/slippage filters executed before new entries (TZ §§3.3, 4.5)."""

    REL_VOLUME_THRESHOLDS: Dict[TradeStyle, float] = {
        TradeStyle.TREND: 1.2,
        TradeStyle.BREAKOUT: 1.2,
        TradeStyle.MEAN_REVERSION: 1.0,
    }

    def __init__(
        self,
        config: FiltersConfig,
        *,
        slippage_auto_reject_factor: float = 1.0,
        slippage_window_min: int = 15,
        latency_max_ms: float = 200.0,
    ) -> None:
        self._thresholds: Dict[SymbolGroup, _LiquidityThreshold] = {
            SymbolGroup.CORE: _LiquidityThreshold(
                min_depth_usd=config.liquidity_core.min_depth_usd,
                max_spread_bps=config.liquidity_core.max_spread_bps,
            ),
            SymbolGroup.PLUS: _LiquidityThreshold(
                min_depth_usd=config.liquidity_plus.min_depth_usd,
                max_spread_bps=config.liquidity_plus.max_spread_bps,
            ),
            SymbolGroup.ROTATION: _LiquidityThreshold(
                min_depth_usd=config.liquidity_rotation.min_depth_usd,
                max_spread_bps=config.liquidity_rotation.max_spread_bps,
            ),
        }
        self._latency_max = min(latency_max_ms, config.max_latency_ms)
        self._max_avg_slippage_bps = config.max_avg_slippage_bps
        self._slippage_factor = slippage_auto_reject_factor
        self._slippage_window_min = slippage_window_min
        self._slippage_violations: Dict[str, datetime] = {}

    def validate(self, state: MarketState, *, trade_style: TradeStyle) -> tuple[bool, list[str]]:
        """Return (is_allowed, reasons) for telemetry/strategy gating."""

        reasons: list[str] = []
        if not self._check_liquidity(state):
            reasons.append("liquidity")
        if not self._check_latency(state):
            reasons.append("latency")
        if not self._check_rel_volume(state, trade_style):
            reasons.append("rel_volume")
        if not self._check_slippage(state):
            reasons.append("slippage")
        return (not reasons, reasons)

    def _check_liquidity(self, state: MarketState) -> bool:
        threshold = self._thresholds[state.group]
        if state.depth_pm1_usd < threshold.min_depth_usd:
            return False
        if state.spread_bps > threshold.max_spread_bps:
            return False
        return True

    def _check_latency(self, state: MarketState) -> bool:
        return state.latency_ms <= self._latency_max

    def _check_rel_volume(self, state: MarketState, style: TradeStyle) -> bool:
        required = self.REL_VOLUME_THRESHOLDS[style]
        return state.rel_volume_5m >= required

    def _check_slippage(self, state: MarketState) -> bool:
        if state.avg_slippage_bps > self._max_avg_slippage_bps:
            return False
        spread_ref = max(state.spread_bps, 1e-9)
        symbol_key = str(state.symbol)
        violation_start = self._slippage_violations.get(symbol_key)
        if state.avg_slippage_bps > self._slippage_factor * spread_ref:
            if violation_start is None:
                self._slippage_violations[symbol_key] = state.timestamp
                return True
            duration_min = (state.timestamp - violation_start).total_seconds() / 60.0
            if duration_min >= self._slippage_window_min:
                return False
            return True
        self._slippage_violations.pop(symbol_key, None)
        return True


__all__ = ["PreTradeFilters", "TradeStyle"]
