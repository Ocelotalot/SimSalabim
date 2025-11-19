from __future__ import annotations

from datetime import datetime, timezone

from app.config.models import StrategyRuntimeConfig
from app.core.enums import Side, StrategyId
from app.core.types import Symbol
from app.execution.execution_engine import ExecutionEngine
from app.execution.models import OrderStatus
from app.risk.models import RiskLimits
from app.risk.risk_engine import RiskEngine
from app.strategies.strategy_a_trend_continuation import StrategyATrendContinuation
from app.telemetry.events import TelemetryEvent, TradeRecord
from app.telemetry.storage import TelemetryStorage


def _limits() -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=25_000,
        per_trade_risk_pct=0.01,
        max_daily_loss_pct=0.1,
        max_concurrent_positions=3,
        cooldown_after_loss_min=5,
        max_leverage=5,
        max_slippage_bps=10,
    )


def test_integration_one_cycle_should_wire_strategy_risk_execution(
    market_state_factory,
    strategy_state_builder,
    fake_gateway,
    telemetry_tmpdir,
) -> None:
    strategy_cfg = StrategyRuntimeConfig(id=StrategyATrendContinuation.id.value, enabled=True, priority=1)
    strategy = StrategyATrendContinuation(strategy_cfg)
    base_state = market_state_factory(
        ATR_14_5m=6.0,
        rel_volume_5m=1.5,
        ADX_15m=30,
        vwap_slope=0.001,
        sigma_vwap=1.0,
        distance_to_vwap=0.4,
        depth_pm1_usd=6_000_000,
        spread_bps=4.0,
    )
    state = strategy_state_builder(base_state, ema20=base_state.mid_price + 0.05, ema50=base_state.mid_price - 0.5)
    market = {Symbol("BTCUSDT"): state}

    signals = strategy.generate_signals(market, {})
    assert signals

    limits = _limits()
    risk = RiskEngine(limits, strategy_configs={StrategyId.STRATEGY_A: strategy_cfg})
    decisions = risk.assess_signals(signals, {}, market)
    decision = decisions[0]
    assert decision.approved

    fake_gateway.next_status = OrderStatus.FILLED
    engine = ExecutionEngine(fake_gateway, limits)
    intent = engine.handle_risk_decision(decision, market[Symbol("BTCUSDT")])
    assert intent is not None
    assert Symbol("BTCUSDT") in engine.positions

    storage = TelemetryStorage(logs_dir=telemetry_tmpdir / "logs", reports_dir=telemetry_tmpdir / "reports")
    storage.append_event(
        TelemetryEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            event_type="entry",
            payload={"symbol": "BTCUSDT"},
            context={"strategy": StrategyId.STRATEGY_A.value},
        )
    )
    storage.append_trade(
        TradeRecord(
            symbol="BTCUSDT",
            strategy_id=StrategyId.STRATEGY_A.value,
            side=Side.LONG,
            entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            exit_time=datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
            qty=1.0,
            entry_price=100.0,
            exit_price=105.0,
            gross_pnl_usdt=5.0,
            net_pnl_usdt=4.5,
            traded_notional_usdt=100.0,
        )
    )
    assert any((telemetry_tmpdir / "logs").iterdir())
    assert any((telemetry_tmpdir / "reports").iterdir())
