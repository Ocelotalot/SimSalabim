"""RiskEngine implementing TZ §4.9 logic and the Signal→Risk bridge."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Mapping, MutableMapping, Sequence
from zoneinfo import ZoneInfo

from app.config.models import StrategyRuntimeConfig
from app.core.enums import Side, StrategyId
from app.core.types import Quantity, Symbol
from app.market.models import MarketState
from app.risk.models import DailyRiskState, PositionState, RiskDecision, RiskLimits
from app.strategies.base import Signal

if False:  # pragma: no cover - only for typing to avoid circular imports
    from app.execution.execution_engine import ExecutionEngine  # noqa: F401


@dataclass(slots=True)
class StrategyPriority:
    """Helper wrapper returning deterministic priority ordering."""

    priorities: Mapping[StrategyId, int]

    def value(self, strategy_id: StrategyId) -> int:
        return self.priorities.get(strategy_id, 10_000)


class RiskEngine:
    """Applies capital/risk constraints to strategy signals (TZ §4.9)."""

    def __init__(
        self,
        limits: RiskLimits,
        strategy_configs: Mapping[StrategyId, StrategyRuntimeConfig] | None = None,
        timezone: str = "Europe/Minsk",
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.limits = limits
        self.tz = ZoneInfo(timezone)
        self._now = now_fn or (lambda: datetime.now(tz=self.tz))
        self.logger = logging.getLogger("bybit_bot.risk")
        priority_map: dict[StrategyId, int] = {}
        for cfg in (strategy_configs or {}).values():
            try:
                strategy_id = StrategyId(cfg.id)
            except ValueError:
                continue
            priority_map[strategy_id] = cfg.priority
        self.priority = StrategyPriority(priority_map)
        now = self._now()
        session_start = datetime(now.year, now.month, now.day, tzinfo=self.tz)
        self.daily_state = DailyRiskState(session_date=session_start)
        self.cooldown_until: datetime | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def assess_signals(
        self,
        signals: Sequence[Signal],
        open_positions: Mapping[Symbol, PositionState],
        market_state: Mapping[Symbol, MarketState],
        now: datetime | None = None,
    ) -> list[RiskDecision]:
        """Return RiskDecision objects honouring TZ §4.9 constraints."""

        now = now or self._now()
        self._roll_session(now)
        decisions: list[RiskDecision] = []
        filtered = self._resolve_conflicts(signals)
        available_slots = max(
            0, self.limits.max_concurrent_positions - sum(1 for pos in open_positions.values() if pos.size)
        )
        rejection_reasons: dict[str, int] = {}
        conflict_rejected = len(signals) - len(filtered)
        if conflict_rejected > 0:
            rejection_reasons["conflict_pruned"] = conflict_rejected
        for signal in filtered:
            decision = self._build_decision(signal, market_state.get(signal.symbol), now)
            if signal.symbol not in open_positions and decision.approved:
                if available_slots <= 0:
                    decision.reject("max_concurrent_positions_reached")
                else:
                    available_slots -= 1
            decisions.append(decision)
            if not decision.approved:
                reason = decision.reason or "unknown"
                rejection_reasons.setdefault(reason, 0)
                rejection_reasons[reason] += 1
        approved_count = sum(1 for d in decisions if d.approved)
        rejected_count = sum(1 for d in decisions if not d.approved)
        self.logger.debug(
            "Risk assessment summary",
            extra={
                "n_signals_in": len(signals),
                "n_conflict_pruned": conflict_rejected,
                "n_approved": approved_count,
                "n_rejected": rejected_count + conflict_rejected,
                "rejection_reasons": rejection_reasons,
            },
        )
        return decisions

    def record_trade_pnl(self, realized_pnl: float, when: datetime | None = None) -> None:
        """Update daily drawdown stats and enforce cooldown after losses."""

        when = when or self._now()
        self._roll_session(when)
        self.daily_state.realized_pnl += realized_pnl
        if realized_pnl < 0:
            self.cooldown_until = when + timedelta(minutes=self.limits.cooldown_after_loss_min)

    def apply_trailing_stop(self, position: PositionState, mkt: MarketState) -> float:
        """Update ``position.current_sl_price`` using trailing rules (TZ §4.9)."""

        mode = (position.trailing_mode or "none").lower()
        if mode == "none":
            return float(position.current_sl_price)

        last_price = float(mkt.mid_price)
        params = position.trailing_params or {}
        if mode == "ema_atr":
            atr = params.get("atr_override", mkt.ATR_14_5m or mkt.atr_q_5m)
            mult = float(params.get("trail_atr_mult", 2.0))
            if atr:
                offset = atr * mult
                target = last_price - offset if position.side is Side.LONG else last_price + offset
                position.update_sl(target)
        elif mode == "percent":
            pct = float(params.get("trail_percent", 0.01))
            if position.side is Side.LONG:
                candidate = last_price * (1 - pct)
            else:
                candidate = last_price * (1 + pct)
            position.update_sl(candidate)
        else:
            # Custom modes can be plugged later; we keep monotonic SL guarantee.
            custom_target = params.get("dynamic_sl_price")
            if custom_target is not None:
                position.update_sl(float(custom_target))
        return float(position.current_sl_price)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _roll_session(self, now: datetime) -> None:
        session_key = datetime(now.year, now.month, now.day, tzinfo=self.tz)
        if session_key.date() != self.daily_state.session_date.date():
            self.daily_state.reset(session_key)
            self.cooldown_until = None

    def _resolve_conflicts(self, signals: Sequence[Signal]) -> list[Signal]:
        """Pick the highest-priority signal per symbol (TZ §4.6.3)."""

        grouped: MutableMapping[Symbol, Signal] = {}
        for signal in sorted(signals, key=lambda s: self.priority.value(s.strategy_id)):
            grouped.setdefault(signal.symbol, signal)
        return list(grouped.values())

    def _build_decision(self, signal: Signal, mkt: MarketState | None, now: datetime) -> RiskDecision:
        decision = RiskDecision(
            signal=signal,
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            side=signal.side,
            entry_type=self._entry_mode(signal),
            size=None,
            notional=None,
            sl_price=signal.sl_price,
            tp_levels=tuple(signal.tp_levels),
            trailing_mode=signal.trailing_mode,
            trailing_params=signal.trailing_params,
            time_stop_bars=signal.time_stop_bars,
            approved=True,
            risk_amount=0.0,
            metadata=signal.metadata,
        )

        if self.daily_state.breach_limit(self.limits):
            return decision.reject("daily_loss_limit")
        if self.cooldown_until and now < self.cooldown_until:
            return decision.reject("cooldown_active")
        if signal.sl_price is None:
            return decision.reject("missing_sl_price")

        risk_amount = self.limits.risk_amount(signal.target_risk_pct, signal.metadata.get("virtual_equity"))
        decision.risk_amount = risk_amount
        if risk_amount <= 0:
            return decision.reject("invalid_risk_amount")

        if signal.target_notional:
            notional = float(signal.target_notional)
        else:
            distance = abs(float(signal.entry_price) - float(signal.sl_price))
            if distance <= 0:
                return decision.reject("zero_sl_distance")
            size = risk_amount / distance
            notional = size * float(signal.entry_price)
        max_notional = self.limits.max_notional(signal.symbol)
        if notional > max_notional:
            notional = max_notional
        if notional <= 0:
            return decision.reject("notional_underflow")
        decision.notional = notional
        decision.size = Quantity(notional / float(signal.entry_price))

        # Slippage guard uses either limits or metadata overrides.
        max_slip = signal.metadata.get("max_slippage_bps", self.limits.max_slippage_bps)
        if max_slip and mkt is not None:
            expected = max(mkt.avg_slippage_bps, mkt.spread_bps * 0.5)
            if expected > max_slip:
                return decision.reject("slippage_filter")
        return decision

    def _entry_mode(self, signal: Signal) -> str:
        meta = signal.metadata or {}
        mode = meta.get("entry_execution") or meta.get("entry_mode")
        return mode or meta.get("entry_type_override", "market_with_cap")


def run_signal_pipeline(
    signals: Sequence[Signal],
    risk_engine: RiskEngine,
    execution_engine: "ExecutionEngine",
    market_state: Mapping[Symbol, MarketState],
    open_positions: Mapping[Symbol, PositionState] | None = None,
    now: datetime | None = None,
) -> list[RiskDecision]:
    """Route signals through risk then execution (Signal→Risk→Execution pipeline)."""

    positions = open_positions or execution_engine.positions
    decisions = risk_engine.assess_signals(signals, positions, market_state, now)
    for decision in decisions:
        if decision.approved:
            execution_engine.handle_risk_decision(decision, market_state.get(decision.symbol), now)
    return decisions


def wire_engines(risk_engine: RiskEngine, execution_engine: "ExecutionEngine") -> "ExecutionEngine":
    """Attach callbacks (PnL + trailing SL) so both engines share state."""

    execution_engine.pnl_callback = risk_engine.record_trade_pnl
    if execution_engine.trailing_callback is None:
        execution_engine.trailing_callback = risk_engine.apply_trailing_stop
    return execution_engine


__all__ = ["RiskEngine", "run_signal_pipeline", "wire_engines"]
