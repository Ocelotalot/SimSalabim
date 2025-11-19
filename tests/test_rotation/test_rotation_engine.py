from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config.models import RotationConfig, SymbolConfig, SymbolGroup
from app.market.models import MarketState
from app.rotation.rotation_engine import RotationEngine


def _symbol(symbol: str, group: SymbolGroup) -> SymbolConfig:
    return SymbolConfig(symbol=symbol, group=group, enabled=True)


def _state(market_state_factory, **overrides) -> MarketState:
    return market_state_factory(**overrides)


def test_rotation_engine_should_compute_scores(market_state_factory, symbol_configs) -> None:
    config = RotationConfig(enabled=True, check_interval_min=1, min_score_for_new_entry=0.4, max_active_symbols=3)
    engine = RotationEngine(config, symbol_configs)
    states = {
        "BTCUSDT": _state(market_state_factory, depth_pm1_usd=5_000_000, spread_bps=2.0, rel_volume_5m=2.0, oi_delta_5m=20_000),
        "ETHUSDT": _state(market_state_factory, depth_pm1_usd=2_000_000, spread_bps=3.0, rel_volume_5m=1.5, oi_delta_5m=10_000),
    }
    rotation_state = engine.update(states, now=datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert set(rotation_state.scores.keys()) == {"BTCUSDT", "ETHUSDT"}
    assert rotation_state.scores["BTCUSDT"].score >= rotation_state.scores["ETHUSDT"].score
    assert rotation_state.active_symbols[0] == "BTCUSDT"


def test_rotation_engine_should_enforce_min_score_and_top_n(market_state_factory) -> None:
    configs = [
        _symbol("BTCUSDT", SymbolGroup.CORE),
        _symbol("ETHUSDT", SymbolGroup.CORE),
        _symbol("SOLUSDT", SymbolGroup.PLUS),
    ]
    config = RotationConfig(enabled=True, check_interval_min=1, min_score_for_new_entry=0.8, max_active_symbols=2)
    engine = RotationEngine(config, configs)
    states = {
        "BTCUSDT": _state(market_state_factory, depth_pm1_usd=5_000_000, spread_bps=2.0, rel_volume_5m=2.0),
        "ETHUSDT": _state(market_state_factory, depth_pm1_usd=1_000_000, spread_bps=6.0, rel_volume_5m=0.5),
        "SOLUSDT": _state(market_state_factory, depth_pm1_usd=1_500_000, spread_bps=5.0, rel_volume_5m=0.8),
    }
    rotation_state = engine.update(states, now=datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert len(rotation_state.active_symbols) == 2
    assert "BTCUSDT" in rotation_state.active_symbols


def test_rotation_engine_should_raise_when_no_market_state(market_state_factory, symbol_configs) -> None:
    config = RotationConfig(enabled=True, check_interval_min=1)
    engine = RotationEngine(config, symbol_configs)
    with pytest.raises(Exception):
        engine.update({}, now=datetime(2024, 1, 1, tzinfo=timezone.utc))
