"""Execution-layer models mapping intents/orders to Bybit calls (ARCH §5.3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from app.core.enums import OrderType, Side, StrategyId, TimeInForce
from app.core.types import Price, Quantity, Symbol
from app.strategies.base import TakeProfitLevel


class EntryIntentStatus(str, Enum):
    """Lifecycle of an entry intent managed by ExecutionEngine."""

    PENDING = "pending"
    ACTIVE = "active"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(slots=True)
class EntryIntent:
    """State machine between risk decision and concrete orders."""

    intent_id: str
    symbol: Symbol
    strategy_id: StrategyId
    side: Side
    size: Quantity
    entry_price: Price
    sl_price: Price
    tp_levels: Sequence[TakeProfitLevel]
    entry_type: str
    created_at: datetime
    ttl_seconds: int
    status: EntryIntentStatus = EntryIntentStatus.PENDING
    metadata: Mapping[str, Any] = field(default_factory=dict)
    expected_slippage_bps: float | None = None
    filled_qty: float = 0.0

    @property
    def expires_at(self) -> datetime:
        return self.created_at + timedelta(seconds=self.ttl_seconds)

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class OrderStatus(str, Enum):
    """Subset of Bybit order lifecycle we care about in v1."""

    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(slots=True)
class OrderIntent:
    """Normalized order request passed to Bybit REST client."""

    symbol: Symbol
    side: Side
    order_type: OrderType
    quantity: Quantity
    price: Price | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    client_order_id: str | None = None
    post_only: bool = False
    trigger_price: Price | None = None
    comment: str | None = None


@dataclass(slots=True)
class ActiveOrder:
    """Track orders submitted to Bybit and their execution status."""

    order_id: str
    intent_id: str
    order: OrderIntent
    status: OrderStatus
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None
    expires_at: datetime | None = None

    def mark_filled(self, qty: float, price: float, now: datetime) -> None:
        self.status = OrderStatus.FILLED
        self.filled_qty = qty
        self.avg_fill_price = price
        self.updated_at = now

    def mark_cancelled(self, now: datetime) -> None:
        self.status = OrderStatus.CANCELLED
        self.updated_at = now


class ExecutionEventType(str, Enum):
    """Semantic events reported back to telemetry/risk."""

    ENTRY_FILLED = "entry_filled"
    ENTRY_CANCELLED = "entry_cancelled"
    ENTRY_REJECTED = "entry_rejected"
    EXIT_FILLED = "exit_filled"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    TIME_STOP = "time_stop"


@dataclass(slots=True)
class ExecutionReport:
    """Lightweight struct describing fills/cancellations."""

    event: ExecutionEventType
    symbol: Symbol
    side: Side
    quantity: float
    price: float
    timestamp: datetime
    intent_id: str | None = None
    order_id: str | None = None
    reason: str | None = None
    slippage_bps: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


__all__ = [
    "EntryIntent",
    "EntryIntentStatus",
    "OrderIntent",
    "OrderStatus",
    "ActiveOrder",
    "ExecutionEventType",
    "ExecutionReport",
]
