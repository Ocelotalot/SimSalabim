from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums import Side, StrategyId
from app.core.types import Symbol
from app.execution.execution_engine import ExecutionEngine
from app.execution.sync_state import hydrate_execution_engine, snapshot_to_position, sync_state_from_exchange
from app.risk.models import RiskLimits


def _limits() -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=10_000,
        per_trade_risk_pct=0.01,
        max_daily_loss_pct=0.1,
        max_concurrent_positions=5,
        cooldown_after_loss_min=5,
        max_leverage=5,
    )


def test_snapshot_to_position_should_normalize_payload() -> None:
    snapshot = {
        "symbol": "BTCUSDT",
        "side": "long",
        "size": 2,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "created_time": int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()),
        "strategy_id": StrategyId.STRATEGY_B.value,
    }
    position = snapshot_to_position(snapshot)
    assert position is not None
    assert position.symbol == Symbol("BTCUSDT")
    assert position.side is Side.LONG
    assert position.initial_sl_price == position.current_sl_price


def test_sync_state_from_exchange_should_fill_engine(fake_gateway) -> None:
    class FakeClient:
        def list_positions(self):
            return [
                {
                    "symbol": "BTCUSDT",
                    "side": "short",
                    "size": 1,
                    "entry_price": 105,
                    "stop_loss": 110,
                    "created_time": int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()),
                }
            ]

    engine = ExecutionEngine(fake_gateway, _limits())
    hydrate_execution_engine(engine, FakeClient())
    assert Symbol("BTCUSDT") in engine.positions
    assert engine.positions[Symbol("BTCUSDT")].side is Side.SHORT


def test_sync_state_should_ignore_zero_size_positions() -> None:
    class FakeClient:
        def list_positions(self):
            return [
                {"symbol": "BTCUSDT", "side": "long", "size": 0, "created_time": 0},
                {"symbol": "ETHUSDT", "side": "short", "size": 2, "created_time": 0},
            ]

    synced = sync_state_from_exchange(FakeClient())
    assert Symbol("BTCUSDT") not in synced
    assert Symbol("ETHUSDT") in synced
