from __future__ import annotations

from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Price, Quantity, Symbol
from app.execution.execution_engine import ExecutionEngine
from app.execution.models import EntryIntentStatus
from app.risk.models import RiskDecision, RiskLimits
from app.strategies.base import Signal


def _limits() -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=10_000,
        per_trade_risk_pct=0.01,
        max_daily_loss_pct=0.1,
        max_concurrent_positions=2,
        cooldown_after_loss_min=5,
        max_leverage=5,
        max_slippage_bps=5,
    )


def _decision() -> RiskDecision:
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
        entry_type="market_with_cap",
        size=Quantity(2.0),
        notional=200.0,
        sl_price=signal.sl_price,
        tp_levels=tuple(),
        trailing_mode=None,
        trailing_params=None,
        time_stop_bars=None,
        approved=True,
        risk_amount=200.0,
        metadata={},
    )


def test_execution_engine_should_reject_when_expected_slippage_high(fake_gateway, market_state_factory) -> None:
    engine = ExecutionEngine(fake_gateway, _limits())
    state = market_state_factory(depth_pm1_usd=10_000, spread_bps=10.0)
    intent = engine.handle_risk_decision(_decision(), state)
    assert intent is not None
    assert intent.status is EntryIntentStatus.REJECTED
