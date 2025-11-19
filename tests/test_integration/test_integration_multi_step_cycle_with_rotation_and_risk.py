from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config.models import RotationConfig, StrategyRuntimeConfig, SymbolConfig, SymbolGroup
from app.core.enums import StrategyId
from app.core.types import Symbol
from app.execution.execution_engine import ExecutionEngine
from app.execution.models import OrderStatus
from app.risk.models import RiskLimits
from app.risk.risk_engine import RiskEngine
from app.rotation.rotation_engine import RotationEngine
from app.runtime.state import RuntimeStateStore
from app.strategies.strategy_a_trend_continuation import StrategyATrendContinuation


def _limits() -> RiskLimits:
    return RiskLimits(
        virtual_equity_usdt=20_000,
        per_trade_risk_pct=0.01,
        max_daily_loss_pct=0.05,
        max_concurrent_positions=2,
        cooldown_after_loss_min=5,
        max_leverage=5,
        max_slippage_bps=10,
    )


def _strategy() -> StrategyATrendContinuation:
    cfg = StrategyRuntimeConfig(id=StrategyATrendContinuation.id.value, enabled=True, priority=1)
    return StrategyATrendContinuation(cfg)


def test_multi_step_cycle_should_update_rotation_and_runtime(
    market_state_factory,
    strategy_state_builder,
    fake_gateway,
    tmp_path,
) -> None:
    rotation = RotationEngine(
        RotationConfig(enabled=True, check_interval_min=1, min_score_for_new_entry=0.4, max_active_symbols=1),
        [
            SymbolConfig(symbol="BTCUSDT", group=SymbolGroup.CORE, enabled=True),
            SymbolConfig(symbol="SOLUSDT", group=SymbolGroup.PLUS, enabled=True),
        ],
    )
    limits = _limits()
    risk = RiskEngine(limits)
    strategy = _strategy()
    engine = ExecutionEngine(fake_gateway, limits)
    fake_gateway.next_status = OrderStatus.FILLED
    runtime_store = RuntimeStateStore(tmp_path)
    runtime_store.save_state(runtime_store.load_state())

    now = datetime(2024, 1, 1, 10, tzinfo=timezone.utc)
    state_step1_raw = {
        Symbol("BTCUSDT"): market_state_factory(depth_pm1_usd=4_000_000, rel_volume_5m=1.6, ATR_14_5m=6.0, ADX_15m=30),
        Symbol("SOLUSDT"): market_state_factory(symbol=Symbol("SOLUSDT"), depth_pm1_usd=2_500_000, rel_volume_5m=1.4, ATR_14_5m=5.5, ADX_15m=28),
    }
    rotation_state1 = rotation.update({str(sym): raw for sym, raw in state_step1_raw.items()}, now=now)
    assert rotation_state1.active_symbols[0] == "BTCUSDT"

    state_step1_strategy = {
        sym: strategy_state_builder(raw, ema20=float(raw.mid_price) + 0.05, ema50=float(raw.mid_price) - 0.5)
        for sym, raw in state_step1_raw.items()
    }
    signals = strategy.generate_signals(state_step1_strategy, engine.positions)
    decisions = risk.assess_signals(signals, engine.positions, state_step1_raw)
    for decision in decisions:
        if decision.approved:
            engine.handle_risk_decision(decision, state_step1_raw[decision.symbol])
    risk.record_trade_pnl(500, when=now + timedelta(minutes=5))
    runtime_store.update_state(daily_pnl_usdt=risk.daily_state.realized_pnl)

    state_step2_raw = {
        Symbol("BTCUSDT"): market_state_factory(depth_pm1_usd=500_000, rel_volume_5m=0.7, ATR_14_5m=3.0, ADX_15m=12),
        Symbol("SOLUSDT"): market_state_factory(symbol=Symbol("SOLUSDT"), depth_pm1_usd=3_500_000, rel_volume_5m=1.8, ATR_14_5m=6.0, ADX_15m=32),
    }
    rotation_state2 = rotation.update({str(sym): raw for sym, raw in state_step2_raw.items()}, now=now + timedelta(minutes=10))
    assert "SOLUSDT" in rotation_state2.active_symbols

    risk.record_trade_pnl(-2_000, when=now + timedelta(minutes=15))
    state_step2_strategy = {
        sym: strategy_state_builder(raw, ema20=float(raw.mid_price) + 0.05, ema50=float(raw.mid_price) - 0.5)
        for sym, raw in state_step2_raw.items()
    }
    signals = strategy.generate_signals(state_step2_strategy, engine.positions)
    decisions = risk.assess_signals(signals, engine.positions, state_step2_raw, now=now + timedelta(minutes=16))
    assert decisions
    assert all(decision.reason == "daily_loss_limit" for decision in decisions if decision.is_rejected)

    runtime_store.update_state(bot_running=False, daily_pnl_usdt=risk.daily_state.realized_pnl)
    runtime_state = runtime_store.load_state()
    assert runtime_state.bot_running is False
    assert runtime_state.daily_pnl_usdt == pytest.approx(risk.daily_state.realized_pnl)
