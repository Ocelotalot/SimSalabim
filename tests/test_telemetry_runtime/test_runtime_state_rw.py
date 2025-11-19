from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.runtime.state import RuntimeState, RuntimeStateStore
from app.telemetry.events import SessionStats


def test_runtime_state_store_should_read_and_write(tmp_path) -> None:
    store = RuntimeStateStore(tmp_path)
    state = store.save_state(RuntimeState(bot_running=True, per_trade_risk_pct=0.01, virtual_equity_usdt=20_000))
    loaded = store.load_state()
    assert loaded.bot_running is True
    assert loaded.virtual_equity_usdt == 20_000

    updated = store.update_state(per_trade_risk_pct=0.02)
    assert updated.per_trade_risk_pct == 0.02

    stats = SessionStats(
        start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        gross_pnl_usdt=100.0,
        net_pnl_usdt=90.0,
        execution_costs_abs=10.0,
        traded_notional_usdt=1_000.0,
        trades_count=2,
        wins=1,
    )
    store.save_session_stats(stats)
    loaded_stats = store.load_session_stats()
    assert loaded_stats is not None
    assert loaded_stats.net_pnl_usdt == 90.0


def test_runtime_state_store_should_validate_fields(tmp_path) -> None:
    store = RuntimeStateStore(tmp_path)
    with pytest.raises(Exception):
        store.update_state(unknown_field=123)
