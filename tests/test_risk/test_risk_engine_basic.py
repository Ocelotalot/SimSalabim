from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config.models import StrategyRuntimeConfig
from app.core.enums import EntryType, Side, StrategyId
from app.core.types import Price, Symbol
from app.market.models import MarketState
from app.risk.models import RiskLimits
from app.risk.risk_engine import RiskEngine
from app.strategies.base import Signal


def _sample_limits() -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=10_000,
        per_trade_risk_pct=0.01,
        max_daily_loss_pct=0.1,
        max_concurrent_positions=2,
        cooldown_after_loss_min=10,
        max_leverage=5,
        max_slippage_bps=10,
        symbol_max_notional_usdt={Symbol("BTCUSDT"): 50_000},
    )


def _market_state(market_state_factory) -> MarketState:
    return market_state_factory(avg_slippage_bps=1.0, spread_bps=2.0)


def _build_signal() -> Signal:
    return Signal(
        symbol=Symbol("BTCUSDT"),
        side=Side.LONG,
        entry_type=EntryType.BREAKOUT,
        strategy_id=StrategyId.STRATEGY_A,
        entry_price=Price(100.0),
        sl_price=Price(95.0),
        target_risk_pct=None,
    )


def test_risk_engine_should_size_notional_from_risk_pct(market_state_factory) -> None:
    limits = _sample_limits()
    configs = {StrategyId.STRATEGY_A: StrategyRuntimeConfig(id=StrategyId.STRATEGY_A.value, enabled=True, priority=1)}
    engine = RiskEngine(limits, strategy_configs=configs)
    decision = engine.assess_signals([_build_signal()], {}, {Symbol("BTCUSDT"): _market_state(market_state_factory)})[0]
    assert decision.approved is True
    assert decision.notional == pytest.approx(2_000.0, rel=1e-3)


def test_risk_engine_should_reject_after_daily_loss(market_state_factory) -> None:
    limits = _sample_limits()
    engine = RiskEngine(limits)
    engine.daily_state.realized_pnl = -2_000
    decision = engine.assess_signals([_build_signal()], {}, {Symbol("BTCUSDT"): _market_state(market_state_factory)})[0]
    assert decision.is_rejected
    assert decision.reason == "daily_loss_limit"
