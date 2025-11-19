"""Strategy registry factory per ARCHITECTURE §2.7 and TZ §4.6."""
from __future__ import annotations

from typing import Dict, Iterable, Mapping

from app.config.models import StrategyRuntimeConfig

from .base import BaseStrategy
from .strategy_a_trend_continuation import StrategyATrendContinuation
from .strategy_b_bb_squeeze import StrategyBBBBSqueeze
from .strategy_c_range_break import StrategyCRangeBreak
from .strategy_d_vwap_mean_reversion import StrategyDVwapMeanReversion
from .strategy_e_liquidity_sweep import StrategyELiquiditySweep

STRATEGY_REGISTRY: Dict[str, type[BaseStrategy]] = {
    StrategyATrendContinuation.id.value: StrategyATrendContinuation,
    StrategyBBBBSqueeze.id.value: StrategyBBBBSqueeze,
    StrategyCRangeBreak.id.value: StrategyCRangeBreak,
    StrategyDVwapMeanReversion.id.value: StrategyDVwapMeanReversion,
    StrategyELiquiditySweep.id.value: StrategyELiquiditySweep,
}


def get_strategy_class(strategy_id: str) -> type[BaseStrategy]:
    """Return strategy class by id, raise if unknown (guard misconfiguration)."""

    try:
        return STRATEGY_REGISTRY[strategy_id]
    except KeyError as exc:  # pragma: no cover - developer error
        raise KeyError(f"Strategy {strategy_id} is not registered") from exc


def build_active_strategies(
    configs: Mapping[str, StrategyRuntimeConfig] | Iterable[StrategyRuntimeConfig],
) -> list[BaseStrategy]:
    """Instantiate enabled strategies sorted by priority ascending.

    The orchestrator iterates strategies in the returned order; the risk engine
    therefore resolves conflicting signals per TZ §2.6 by preferring the
    smallest `priority` (highest importance). Lower-priority signals for the
    same symbol are tagged as `skipped_due_to_conflict`.
    """

    if isinstance(configs, Mapping):
        source = configs.values()
    else:
        source = configs
    active: list[BaseStrategy] = []
    for cfg in source:
        if not cfg.enabled:
            continue
        cls = STRATEGY_REGISTRY.get(cfg.id)
        if cls is None:
            continue
        active.append(cls(cfg))
    active.sort(key=lambda strat: strat.runtime_config.priority)
    return active
