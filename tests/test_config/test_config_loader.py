from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.config.loader import (
    load_app_config,
    load_secrets_config,
    load_strategies_config,
    load_symbols_config,
    load_trading_config,
)
from app.config.models import BybitMode


def _write_yaml(path: Path, content: str) -> Path:
    path.write_text(dedent(content), encoding="utf-8")
    return path


def test_load_trading_config_should_parse_valid_yaml(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "trading.yml",
        """
        bybit:
          mode: live
        risk:
          virtual_equity_usdt: 20000
          per_trade_risk_pct: 0.01
          max_daily_loss_pct: 0.05
          max_concurrent_positions: 2
        rotation:
          enabled: true
          check_interval_min: 5
          min_score_for_new_entry: 0.55
          max_active_symbols: 3
        filters:
          liquidity_core:
            min_depth_usd: 3000000
            max_spread_bps: 3
          liquidity_plus:
            min_depth_usd: 1000000
            max_spread_bps: 5
          liquidity_rotation:
            min_depth_usd: 1000000
            max_spread_bps: 6
          max_latency_ms: 250
          max_avg_slippage_bps: 5
        tf_profiles:
          aggr:
            trigger_tf: 1m
            confirm_tf: 3m
            slow_tf: 5m
          bal:
            trigger_tf: 3m
            confirm_tf: 5m
            slow_tf: 15m
          cons:
            trigger_tf: 5m
            confirm_tf: 15m
            slow_tf: 60m
        telemetry:
          stats_interval_min: 60
          log_level: INFO
          reports_dir: data/logs
        """,
    )
    config = load_trading_config(path)
    assert config.bybit.mode is BybitMode.LIVE
    assert config.risk.virtual_equity_usdt == 20_000
    assert config.rotation.max_active_symbols == 3
    assert config.filters.max_latency_ms == 250
    assert config.tf_profiles.aggr.trigger_tf == "1m"
    assert config.telemetry.reports_dir == "data/logs"


def test_load_symbols_config_should_require_symbols_key(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path / "symbols.yml", "{}")
    with pytest.raises(ValueError):
        load_symbols_config(path)


def test_load_strategies_config_should_index_by_id(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "strategies.yml",
        """
        strategies:
          - id: strategy_a_trend_continuation
            enabled: true
            priority: 1
          - id: strategy_b_bb_squeeze
            enabled: false
            priority: 3
        """,
    )
    strategies = load_strategies_config(path)
    assert set(strategies) == {
        "strategy_a_trend_continuation",
        "strategy_b_bb_squeeze",
    }
    assert strategies["strategy_a_trend_continuation"].enabled is True
    assert strategies["strategy_b_bb_squeeze"].enabled is False


def test_load_app_config_should_merge_all_sections(tmp_path: Path) -> None:
    trading_path = _write_yaml(
        tmp_path / "trading.yml",
        """
        bybit:
          mode: demo
        risk:
          virtual_equity_usdt: 15000
          per_trade_risk_pct: 0.005
          max_daily_loss_pct: 0.02
          max_concurrent_positions: 2
        rotation:
          enabled: false
          check_interval_min: 15
          min_score_for_new_entry: 0.5
          max_active_symbols: 2
        filters:
          liquidity_core:
            min_depth_usd: 2000000
            max_spread_bps: 4
          liquidity_plus:
            min_depth_usd: 1000000
            max_spread_bps: 5
          liquidity_rotation:
            min_depth_usd: 500000
            max_spread_bps: 7
          max_latency_ms: 300
          max_avg_slippage_bps: 6
        tf_profiles:
          aggr:
            trigger_tf: 1m
            confirm_tf: 3m
            slow_tf: 5m
          bal:
            trigger_tf: 3m
            confirm_tf: 5m
            slow_tf: 15m
          cons:
            trigger_tf: 5m
            confirm_tf: 15m
            slow_tf: 60m
        """,
    )
    symbols_path = _write_yaml(
        tmp_path / "symbols.yml",
        """
        symbols:
          - symbol: BTCUSDT
            group: core
            enabled: true
          - symbol: ARBUSDT
            group: rotation
        """,
    )
    strategies_path = _write_yaml(
        tmp_path / "strategies.yml",
        """
        strategies:
          - id: strategy_a_trend_continuation
            enabled: true
            priority: 1
        """,
    )
    secrets_path = _write_yaml(
        tmp_path / "secrets.yaml",
        """
        bybit:
          demo:
            api_key: DEMO_KEY
            api_secret: DEMO_SECRET
          live:
            api_key: LIVE_KEY
            api_secret: LIVE_SECRET
        telegram:
          bot_token: TOKEN123456
          chat_id: 123456
        """,
    )
    app_config = load_app_config(
        trading_path=trading_path,
        symbols_path=symbols_path,
        strategies_path=strategies_path,
        secrets_path=secrets_path,
    )
    assert app_config.trading.bybit.mode is BybitMode.DEMO
    assert len(app_config.symbols) == 2
    assert "strategy_a_trend_continuation" in app_config.strategies
    assert app_config.credentials.bybit.demo.api_key == "DEMO_KEY"


def test_load_secrets_config_should_require_demo_and_live(tmp_path: Path) -> None:
    path = _write_yaml(
        tmp_path / "secrets.yaml",
        """
        bybit:
          demo:
            api_key: DEMO12345
            api_secret: DEMO_SECRET
          live:
            api_key: LIVE67890
            api_secret: LIVE_SECRET
        telegram:
          bot_token: TOKEN123456
          chat_id: 999
        """,
    )
    secrets = load_secrets_config(path)
    assert secrets.bybit.demo.api_key == "DEMO12345"
    assert secrets.bybit.live.api_secret == "LIVE_SECRET"
    assert secrets.telegram.chat_id == 999
