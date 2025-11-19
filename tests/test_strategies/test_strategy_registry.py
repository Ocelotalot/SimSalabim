from __future__ import annotations

from app.config.models import StrategyRuntimeConfig
from app.core.enums import StrategyId
from app.strategies.registry import STRATEGY_REGISTRY, build_active_strategies


def test_strategy_registry_should_include_all_ids() -> None:
    expected = {strategy_id.value for strategy_id in StrategyId}
    assert set(STRATEGY_REGISTRY.keys()) == expected


def test_build_active_strategies_should_respect_priority() -> None:
    configs = [
        StrategyRuntimeConfig(id=StrategyId.STRATEGY_B.value, enabled=True, priority=2),
        StrategyRuntimeConfig(id=StrategyId.STRATEGY_A.value, enabled=True, priority=1),
        StrategyRuntimeConfig(id="unknown", enabled=True, priority=3),
        StrategyRuntimeConfig(id=StrategyId.STRATEGY_C.value, enabled=False, priority=3),
    ]
    active = build_active_strategies(configs)
    assert [strategy.id for strategy in active] == [StrategyId.STRATEGY_A, StrategyId.STRATEGY_B]
