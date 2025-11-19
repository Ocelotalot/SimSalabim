from __future__ import annotations

from datetime import datetime, timezone

from app.config.models import StrategyRuntimeConfig
from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Price, Quantity, Symbol
from app.risk.models import PositionState, RiskLimits
from app.risk.risk_engine import RiskEngine
from app.strategies.base import Signal


def _limits(max_positions: int = 1) -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=20_000,
        per_trade_risk_pct=0.01,
        max_daily_loss_pct=0.1,
        max_concurrent_positions=max_positions,
        cooldown_after_loss_min=5,
        max_leverage=5,
    )


def _signal(strategy: StrategyId, side: Side) -> Signal:
    return Signal(
        symbol=Symbol("BTCUSDT"),
        side=side,
        entry_type=EntryType.BREAKOUT,
        strategy_id=strategy,
        entry_price=Price(100.0),
        sl_price=Price(95.0),
    )


def _position(symbol: str) -> PositionState:
    return PositionState(
        symbol=Symbol(symbol),
        strategy_id=StrategyId.STRATEGY_A,
        side=Side.LONG,
        size=Quantity(1),
        entry_price=Price(100.0),
        open_time=datetime.now(tz=timezone.utc),
        initial_sl_price=Price(95.0),
        current_sl_price=Price(95.0),
    )


def test_risk_engine_should_prefer_higher_priority_signal(market_state_factory) -> None:
    limits = _limits(max_positions=2)
    configs = {
        StrategyId.STRATEGY_A: StrategyRuntimeConfig(id=StrategyId.STRATEGY_A.value, enabled=True, priority=1),
        StrategyId.STRATEGY_B: StrategyRuntimeConfig(id=StrategyId.STRATEGY_B.value, enabled=True, priority=3),
    }
    engine = RiskEngine(limits, strategy_configs=configs)
    market = {Symbol("BTCUSDT"): market_state_factory()}
    decisions = engine.assess_signals([
        _signal(StrategyId.STRATEGY_B, Side.SHORT),
        _signal(StrategyId.STRATEGY_A, Side.LONG),
    ], {}, market)
    assert len(decisions) == 1
    assert decisions[0].strategy_id is StrategyId.STRATEGY_A


def test_risk_engine_should_enforce_max_concurrent_positions(market_state_factory) -> None:
    limits = _limits(max_positions=1)
    engine = RiskEngine(limits)
    open_positions = {Symbol("ETHUSDT"): _position("ETHUSDT")}
    decision = engine.assess_signals([
        _signal(StrategyId.STRATEGY_A, Side.LONG),
    ], open_positions, {Symbol("BTCUSDT"): market_state_factory()})[0]
    assert decision.is_rejected
    assert decision.reason == "max_concurrent_positions_reached"
