from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Price, Quantity, Symbol
from app.execution.execution_engine import ExecutionEngine
from app.execution.models import ActiveOrder, EntryIntentStatus, ExecutionEventType, OrderStatus
from app.risk.models import RiskDecision, RiskLimits
from app.strategies.base import Signal


def _limits() -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=20_000,
        per_trade_risk_pct=0.01,
        max_daily_loss_pct=0.1,
        max_concurrent_positions=5,
        cooldown_after_loss_min=5,
        max_leverage=5,
        max_slippage_bps=10,
        symbol_max_notional_usdt={},
    )


def _decision(entry_type: str) -> RiskDecision:
    signal = Signal(
        symbol=Symbol("BTCUSDT"),
        side=Side.LONG,
        entry_type=EntryType.BREAKOUT,
        strategy_id=StrategyId.STRATEGY_A,
        entry_price=Price(100.0),
        sl_price=Price(95.0),
    )
    return RiskDecision(
        signal=signal,
        strategy_id=signal.strategy_id,
        symbol=signal.symbol,
        side=signal.side,
        entry_type=entry_type,
        size=Quantity(1.0),
        notional=100.0,
        sl_price=signal.sl_price,
        tp_levels=tuple(),
        trailing_mode=None,
        trailing_params=None,
        time_stop_bars=5,
        approved=True,
        risk_amount=100.0,
        metadata={"entry_ttl_seconds": 1},
    )


def test_execution_engine_should_activate_limit_on_retest(fake_gateway, market_state_factory) -> None:
    engine = ExecutionEngine(fake_gateway, _limits())
    decision = _decision("limit_on_retest")
    intent = engine.handle_risk_decision(decision, market_state_factory(mid_price=101.0))
    assert intent is not None
    assert intent.status is EntryIntentStatus.PENDING

    fake_gateway.next_status = OrderStatus.NEW
    reports = engine.on_market_snapshot({Symbol("BTCUSDT"): market_state_factory(mid_price=99.0)}, now=intent.created_at)
    assert intent.status is EntryIntentStatus.ACTIVE
    assert fake_gateway.submitted
    assert reports == []

    intent.created_at -= timedelta(seconds=5)
    cancel_reports = engine.on_market_snapshot(
        {Symbol("BTCUSDT"): market_state_factory(mid_price=120.0)},
        now=intent.created_at + timedelta(seconds=2),
    )
    assert any(report.reason == "limit_on_retest_ttl" for report in cancel_reports)


def test_execution_engine_should_handle_partial_fill_on_limit(fake_gateway, market_state_factory) -> None:
    engine = ExecutionEngine(fake_gateway, _limits())
    decision = _decision("limit_on_retest")
    intent = engine.handle_risk_decision(decision, market_state_factory(mid_price=101.0))
    fake_gateway.next_status = OrderStatus.NEW
    engine.on_market_snapshot({Symbol("BTCUSDT"): market_state_factory(mid_price=99.0)})

    order = fake_gateway.submitted[-1]
    active_order = ActiveOrder(
        order_id="order-partial",
        intent_id=intent.intent_id,
        order=order,
        status=OrderStatus.CANCELLED,
        filled_qty=0.5,
        avg_fill_price=99.5,
    )

    reports = engine.handle_order_update(active_order)
    assert {report.event for report in reports} == {ExecutionEventType.ENTRY_FILLED, ExecutionEventType.ENTRY_CANCELLED}
