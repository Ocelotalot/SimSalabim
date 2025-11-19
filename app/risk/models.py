"""Risk models shared between strategies, risk-engine and execution layers.

The module consolidates strongly typed dataclasses that describe positions,
limits, and risk decisions per ARCHITECTURE.md §5.3 and TZ.txt §4.9.  The rest of
risk/execution subsystems import these models to ensure consistent contracts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, MutableMapping, Sequence

from app.core.enums import Side, StrategyId
from app.core.types import Price, Quantity, Symbol
from app.strategies.base import Signal, TakeProfitLevel


@dataclass(slots=True)
class PositionLeg:
    """Single fill composing a net position (TZ §4.7.6)."""

    size: Quantity
    entry_price: Price
    fill_time: datetime
    fee_paid: float = 0.0
    sl_price: Price | None = None
    tp_price: Price | None = None

    def notional(self) -> float:
        """Return contract value in quote currency."""

        return float(self.size) * float(self.entry_price)


@dataclass(slots=True)
class PositionState:
    """Runtime state tracked for each open position (TZ §4.6.1, §4.9)."""

    symbol: Symbol
    strategy_id: StrategyId
    side: Side
    size: Quantity
    entry_price: Price
    open_time: datetime
    initial_sl_price: Price
    current_sl_price: Price
    trailing_mode: str | None = None
    trailing_params: Mapping[str, Any] = field(default_factory=dict)
    tp_levels: Sequence[TakeProfitLevel] = field(default_factory=tuple)
    time_stop_at: datetime | None = None
    metadata: MutableMapping[str, Any] = field(default_factory=dict)
    legs: list[PositionLeg] = field(default_factory=list)
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    def __post_init__(self) -> None:
        if not self.legs:
            self.legs = [
                PositionLeg(
                    size=self.size,
                    entry_price=self.entry_price,
                    fill_time=self.open_time,
                    sl_price=self.initial_sl_price,
                )
            ]

    def risk_per_unit(self) -> float:
        """Return price distance between entry and SL (1R)."""

        return abs(float(self.entry_price) - float(self.initial_sl_price))

    def update_sl(self, new_price: float) -> None:
        """Apply monotonic SL update respecting side semantics (TZ §4.9)."""

        if self.side is Side.LONG:
            if new_price > float(self.current_sl_price):
                self.current_sl_price = Price(new_price)
        else:
            if new_price < float(self.current_sl_price):
                self.current_sl_price = Price(new_price)

    def remaining_size(self) -> float:
        return float(self.size)

    def reduce(self, fraction: float) -> float:
        """Reduce size by ``fraction`` and return closed quantity."""

        if not 0 < fraction <= 1:
            raise ValueError("fraction must be (0,1]")
        closed_qty = float(self.size) * fraction
        new_size = max(0.0, float(self.size) - closed_qty)
        self.size = Quantity(new_size)
        return closed_qty


@dataclass(slots=True)
class RiskLimits:
    """Configuration snapshot with helper accessors (TZ §4.9)."""

    virtual_equity_usdt: float
    per_trade_risk_pct: float
    max_daily_loss_pct: float
    max_concurrent_positions: int
    cooldown_after_loss_min: int
    max_leverage: int
    max_slippage_bps: float | None = None
    symbol_max_notional_usdt: Mapping[Symbol, float] = field(default_factory=dict)

    def risk_amount(self, override_pct: float | None = None, equity: float | None = None) -> float:
        base_pct = override_pct if override_pct is not None else self.per_trade_risk_pct
        equity_value = equity if equity is not None else self.virtual_equity_usdt
        return base_pct * equity_value

    def max_notional(self, symbol: Symbol) -> float:
        base_cap = self.virtual_equity_usdt * self.max_leverage
        sym_cap = self.symbol_max_notional_usdt.get(symbol)
        return min(base_cap, sym_cap) if sym_cap else base_cap

    @property
    def daily_loss_limit(self) -> float:
        return -self.virtual_equity_usdt * self.max_daily_loss_pct


@dataclass(slots=True)
class DailyRiskState:
    """Tracks realized PnL and session rollover for TZ-defined daily checks."""

    session_date: datetime
    realized_pnl: float = 0.0

    def reset(self, new_session: datetime) -> None:
        self.session_date = new_session
        self.realized_pnl = 0.0

    def breach_limit(self, limits: RiskLimits) -> bool:
        return self.realized_pnl <= limits.daily_loss_limit


@dataclass(slots=True)
class RiskDecision:
    """Result of applying risk filters to a signal (ARCHITECTURE §5.3)."""

    signal: Signal
    strategy_id: StrategyId
    symbol: Symbol
    side: Side
    entry_type: str
    size: Quantity | None
    notional: float | None
    sl_price: Price | None
    tp_levels: Sequence[TakeProfitLevel]
    trailing_mode: str | None
    trailing_params: Mapping[str, Any] | None
    time_stop_bars: int | None
    approved: bool
    risk_amount: float = 0.0
    reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def reject(self, reason: str) -> "RiskDecision":
        self.approved = False
        self.reason = reason
        self.size = None
        self.notional = None
        return self

    @property
    def is_rejected(self) -> bool:
        return not self.approved


__all__ = [
    "PositionLeg",
    "PositionState",
    "RiskLimits",
    "RiskDecision",
    "DailyRiskState",
]
