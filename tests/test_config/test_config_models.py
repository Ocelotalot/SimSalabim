from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config.models import (
    BybitMode,
    BybitTradingConfig,
    FiltersConfig,
    LiquidityThresholdConfig,
    RiskConfig,
    RotationConfig,
    StrategyRuntimeConfig,
    SymbolConfig,
    SymbolGroup,
    TfProfileSettings,
    TfProfilesConfig,
    TradingConfig,
)


def test_symbol_config_rotation_disabled_by_default() -> None:
    cfg = SymbolConfig(symbol="ARBUSDT", group=SymbolGroup.ROTATION)
    assert cfg.enabled is False


def test_symbol_config_core_enabled_by_default() -> None:
    cfg = SymbolConfig(symbol="BTCUSDT", group=SymbolGroup.CORE)
    assert cfg.enabled is True


def test_risk_config_should_validate_bounds() -> None:
    with pytest.raises(ValidationError):
        RiskConfig(
            virtual_equity_usdt=10_000,
            per_trade_risk_pct=-0.1,
            max_daily_loss_pct=0.2,
            max_concurrent_positions=1,
        )
    with pytest.raises(ValidationError):
        RiskConfig(
            virtual_equity_usdt=10_000,
            per_trade_risk_pct=0.05,
            max_daily_loss_pct=1.2,
            max_concurrent_positions=1,
        )


def test_trading_config_should_apply_defaults() -> None:
    config = TradingConfig(
        bybit=BybitTradingConfig(mode=BybitMode.DEMO),
        risk=RiskConfig(
            virtual_equity_usdt=15_000,
            per_trade_risk_pct=0.01,
            max_daily_loss_pct=0.05,
            max_concurrent_positions=2,
        ),
        rotation=RotationConfig(),
        filters=FiltersConfig(
            liquidity_core=LiquidityThresholdConfig(min_depth_usd=3_000_000, max_spread_bps=3.0),
            liquidity_plus=LiquidityThresholdConfig(min_depth_usd=1_000_000, max_spread_bps=5.0),
            liquidity_rotation=LiquidityThresholdConfig(min_depth_usd=750_000, max_spread_bps=6.0),
        ),
        tf_profiles=TfProfilesConfig(
            aggr=TfProfileSettings(trigger_tf="1m", confirm_tf="3m", slow_tf="5m"),
            bal=TfProfileSettings(trigger_tf="3m", confirm_tf="5m", slow_tf="15m"),
            cons=TfProfileSettings(trigger_tf="5m", confirm_tf="15m", slow_tf="60m"),
        ),
    )
    assert config.update_interval_sec == 15
    assert config.telemetry.stats_interval_min == 60


def test_strategy_runtime_config_requires_priority() -> None:
    with pytest.raises(ValidationError):
        StrategyRuntimeConfig(id="strategy_x", enabled=True, priority=0)
