from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Price, Symbol
from app.risk.models import RiskLimits
from app.risk.risk_engine import RiskEngine
from app.strategies.base import Signal


def _limits() -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=15_000,
        per_trade_risk_pct=0.02,
        max_daily_loss_pct=0.1,
        max_concurrent_positions=2,
        cooldown_after_loss_min=30,
        max_leverage=5,
        max_slippage_bps=5,
        symbol_max_notional_usdt={Symbol("BTCUSDT"): 500.0},
    )


def _signal() -> Signal:
    return Signal(
        symbol=Symbol("BTCUSDT"),
        side=Side.LONG,
        entry_type=EntryType.BREAKOUT,
        strategy_id=StrategyId.STRATEGY_A,
        entry_price=Price(100.0),
        sl_price=Price(90.0),
    )


def test_risk_engine_should_apply_cooldown_after_loss(market_state_factory) -> None:
    limits = _limits()
    engine = RiskEngine(limits)
    now = datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    engine.record_trade_pnl(-500, when=now)
    decision = engine.assess_signals([
        _signal(),
    ], {}, {Symbol("BTCUSDT"): market_state_factory()}, now=now + timedelta(minutes=5))[0]
    assert decision.is_rejected
    assert decision.reason == "cooldown_active"


def test_risk_engine_should_cap_notional_by_symbol_limit(market_state_factory) -> None:
    limits = _limits()
    engine = RiskEngine(limits)
    decision = engine.assess_signals([
        _signal(),
    ], {}, {Symbol("BTCUSDT"): market_state_factory(avg_slippage_bps=1.0, spread_bps=2.0)})[0]
    assert decision.notional == pytest.approx(500.0)
