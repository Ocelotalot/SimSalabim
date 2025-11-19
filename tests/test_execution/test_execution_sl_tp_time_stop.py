from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Price, Quantity, Symbol
from app.execution.execution_engine import ExecutionEngine
from app.execution.models import ExecutionEventType, OrderStatus
from app.risk.models import RiskDecision, RiskLimits
from app.strategies.base import Signal, TakeProfitLevel


def _limits() -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=50_000,
        per_trade_risk_pct=0.02,
        max_daily_loss_pct=0.1,
        max_concurrent_positions=5,
        cooldown_after_loss_min=5,
        max_leverage=5,
        max_slippage_bps=10,
    )


def _decision() -> RiskDecision:
    signal = Signal(
        symbol=Symbol("BTCUSDT"),
        side=Side.LONG,
        entry_type=EntryType.BREAKOUT,
        strategy_id=StrategyId.STRATEGY_A,
        entry_price=Price(100.0),
        sl_price=Price(95.0),
        tp_levels=(
            TakeProfitLevel(price=105.0, size_pct=0.5, label="tp1"),
            TakeProfitLevel(price=110.0, size_pct=0.25, label="tp2"),
        ),
    )
    return RiskDecision(
        signal=signal,
        strategy_id=signal.strategy_id,
        symbol=signal.symbol,
        side=signal.side,
        entry_type="market_with_cap",
        size=Quantity(1.0),
        notional=100.0,
        sl_price=signal.sl_price,
        tp_levels=signal.tp_levels,
        trailing_mode=None,
        trailing_params=None,
        time_stop_bars=5,
        approved=True,
        risk_amount=100.0,
        metadata={"time_stop_bar_seconds": 60},
    )


def test_execution_engine_should_manage_tp_sl_and_time_stop(fake_gateway, market_state_factory) -> None:
    fake_gateway.next_status = OrderStatus.FILLED
    engine = ExecutionEngine(fake_gateway, _limits())
    decision = _decision()
    now = datetime(2024, 1, 1, 10, 0, 0)
    intent = engine.handle_risk_decision(decision, market_state_factory(mid_price=100.0), now=now)
    assert intent is not None
    assert Symbol("BTCUSDT") in engine.positions

    reports = engine.on_market_snapshot({Symbol("BTCUSDT"): market_state_factory(mid_price=105.0)}, now=now + timedelta(minutes=1))
    assert any(report.event is ExecutionEventType.TAKE_PROFIT for report in reports)

    reports = engine.on_market_snapshot({Symbol("BTCUSDT"): market_state_factory(mid_price=110.0)}, now=now + timedelta(minutes=2))
    assert any(
        report.event is ExecutionEventType.TAKE_PROFIT and pytest.approx(report.quantity, rel=1e-6) == 0.125
        for report in reports
    )

    reports = engine.on_market_snapshot({Symbol("BTCUSDT"): market_state_factory(mid_price=94.0)}, now=now + timedelta(minutes=3))
    assert any(report.event is ExecutionEventType.STOP_LOSS for report in reports)

    # recreate position to test time-stop
    fake_gateway.next_status = OrderStatus.FILLED
    intent = engine.handle_risk_decision(_decision(), market_state_factory(mid_price=100.0), now=now)
    reports = engine.on_market_snapshot(
        {Symbol("BTCUSDT"): market_state_factory(mid_price=101.0)},
        now=now + timedelta(minutes=5),
    )
    assert any(report.event is ExecutionEventType.TIME_STOP for report in reports)
