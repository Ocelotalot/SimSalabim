from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core.enums import Side
from app.telemetry.events import SessionStats, TelemetryEvent, TradeRecord
from app.telemetry.storage import TelemetryStorage


def test_telemetry_storage_should_write_event_trade_and_stats(telemetry_tmpdir) -> None:
    runtime_stats_path = telemetry_tmpdir / "runtime" / "session_stats.json"
    storage = TelemetryStorage(
        logs_dir=telemetry_tmpdir / "logs",
        reports_dir=telemetry_tmpdir / "reports",
        runtime_session_stats_path=runtime_stats_path,
    )
    event = TelemetryEvent(
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        event_type="risk",
        payload={"reason": "cooldown"},
        context={"symbol": "BTCUSDT"},
    )
    event_path = storage.append_event(event)
    content = event_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert json.loads(content)["event_type"] == "risk"

    trade = TradeRecord(
        symbol="BTCUSDT",
        strategy_id="strategy_a",
        side=Side.LONG,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
        qty=1.0,
        entry_price=100.0,
        exit_price=110.0,
        gross_pnl_usdt=10.0,
        net_pnl_usdt=9.5,
        traded_notional_usdt=100.0,
    )
    trade_path = storage.append_trade(trade)
    assert "trades_" in trade_path.name
    assert "BTCUSDT" in trade_path.read_text(encoding="utf-8")

    stats = SessionStats.from_trades([trade], start_time=trade.entry_time, end_time=trade.exit_time)
    stats_path = storage.write_session_stats(stats)
    saved = json.loads(stats_path.read_text(encoding="utf-8"))
    assert saved["trades_count"] == 1
    assert saved["gross_pnl_usdt"] == 10.0
    runtime_saved = json.loads(runtime_stats_path.read_text(encoding="utf-8"))
    assert runtime_saved["net_pnl_usdt"] == saved["net_pnl_usdt"]
