"""ExecutionEngine orchestrating intents, orders and position lifecycle."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Mapping, MutableMapping, Protocol

from app.core.enums import OrderType, Side, TimeInForce
from app.core.types import Price, Quantity, Symbol
from app.execution.models import (
    ActiveOrder,
    EntryIntent,
    EntryIntentStatus,
    ExecutionEventType,
    ExecutionReport,
    OrderIntent,
    OrderStatus,
)
from app.market.models import MarketState
from app.risk.models import PositionState, RiskDecision, RiskLimits


class OrderGateway(Protocol):
    """Minimal interface abstracting Bybit REST/WebSocket clients."""

    def submit_order(self, order: OrderIntent) -> ActiveOrder: ...

    def cancel_order(self, order_id: str) -> None: ...


@dataclass(slots=True)
class ExecutionEngine:
    """Converts RiskDecision objects into orders and position updates."""

    gateway: OrderGateway
    limits: RiskLimits
    trailing_callback: Callable[[PositionState, MarketState], float] | None = None
    pnl_callback: Callable[[float, datetime], None] | None = None
    default_entry_ttl_sec: int = 300

    def __post_init__(self) -> None:
        self.positions: MutableMapping[Symbol, PositionState] = {}
        self.entry_intents: MutableMapping[str, EntryIntent] = {}
        self.active_orders: MutableMapping[str, ActiveOrder] = {}

    # ------------------------------------------------------------------
    # Public orchestrator
    # ------------------------------------------------------------------
    def handle_risk_decision(
        self,
        decision: RiskDecision,
        mkt: MarketState | None,
        now: datetime | None = None,
    ) -> EntryIntent | None:
        """Create EntryIntent per decision and route to proper execution path."""

        if not decision.approved or decision.size is None or decision.sl_price is None:
            return None
        now = now or datetime.utcnow()
        metadata = dict(decision.metadata or {})
        if decision.time_stop_bars is not None:
            metadata.setdefault("time_stop_bars", decision.time_stop_bars)
        if decision.trailing_mode is not None:
            metadata.setdefault("trailing_mode", decision.trailing_mode)
        if decision.trailing_params:
            metadata.setdefault("trailing_params", decision.trailing_params)
        metadata.setdefault("strategy_id", decision.strategy_id.value)
        ttl = int(metadata.get("entry_ttl_seconds", self.default_entry_ttl_sec))
        intent = EntryIntent(
            intent_id=str(uuid.uuid4()),
            symbol=decision.symbol,
            strategy_id=decision.strategy_id,
            side=decision.side,
            size=decision.size,
            entry_price=decision.signal.entry_price,
            sl_price=decision.sl_price,
            tp_levels=decision.tp_levels,
            entry_type=decision.entry_type,
            created_at=now,
            ttl_seconds=ttl,
            metadata=metadata,
        )
        self.entry_intents[intent.intent_id] = intent
        if intent.entry_type == "market_with_cap":
            self._execute_market_intent(intent, mkt, decision)
        elif intent.entry_type == "limit_on_retest":
            self._track_limit_intent(intent, mkt)
        else:
            # Default to market execution for unknown types.
            self._execute_market_intent(intent, mkt, decision)
        return intent

    def on_market_snapshot(
        self,
        market_state: Mapping[Symbol, MarketState],
        now: datetime | None = None,
    ) -> list[ExecutionReport]:
        """Process price updates for limit triggers, SL/TP, trailing and time-stop."""

        now = now or datetime.utcnow()
        reports: list[ExecutionReport] = []
        reports.extend(self._activate_limit_retests(market_state, now))
        for symbol, position in list(self.positions.items()):
            mkt = market_state.get(symbol)
            if not mkt:
                continue
            if self.trailing_callback:
                self.trailing_callback(position, mkt)
            reports.extend(self._evaluate_position_targets(position, mkt, now))
        return reports

    def handle_order_update(self, order: ActiveOrder, now: datetime | None = None) -> list[ExecutionReport]:
        """Update state based on Bybit execution callback (partial fills, etc.)."""

        now = now or datetime.utcnow()
        reports: list[ExecutionReport] = []
        self.active_orders[order.order_id] = order
        intent = self.entry_intents.get(order.intent_id)
        if intent is None:
            return reports
        if order.status == OrderStatus.FILLED:
            reports.append(self._open_position_from_fill(intent, order.avg_fill_price, order.filled_qty, now))
            self.entry_intents.pop(intent.intent_id, None)
        elif order.status == OrderStatus.CANCELLED:
            filled_qty = max(order.filled_qty or 0.0, 0.0)
            remaining_qty = max(float(intent.size) - filled_qty, 0.0)
            if filled_qty > 0:
                avg_price = order.avg_fill_price or float(intent.entry_price)
                reports.append(self._open_position_from_fill(intent, avg_price, filled_qty, now))
            intent.status = EntryIntentStatus.CANCELLED
            reports.append(
                ExecutionReport(
                    event=ExecutionEventType.ENTRY_CANCELLED,
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=remaining_qty,
                    price=float(intent.entry_price),
                    timestamp=now,
                    intent_id=intent.intent_id,
                    order_id=order.order_id,
                    reason="order_cancelled",
                )
            )
            self.entry_intents.pop(intent.intent_id, None)
        elif order.status == OrderStatus.REJECTED:
            intent.status = EntryIntentStatus.REJECTED
            reports.append(
                ExecutionReport(
                    event=ExecutionEventType.ENTRY_REJECTED,
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=0.0,
                    price=float(intent.entry_price),
                    timestamp=now,
                    intent_id=intent.intent_id,
                    order_id=order.order_id,
                    reason="gateway_rejected",
                )
            )
            self.entry_intents.pop(intent.intent_id, None)
        else:
            # Partial fills accumulate until Bybit sends a final FILLED update.
            intent.status = EntryIntentStatus.ACTIVE
            intent.filled_qty = order.filled_qty
        return reports

    # ------------------------------------------------------------------
    # Position/state helpers
    # ------------------------------------------------------------------
    def _open_position_from_fill(
        self,
        intent: EntryIntent,
        fill_price: float,
        qty: float,
        now: datetime,
    ) -> ExecutionReport:
        position = PositionState(
            symbol=intent.symbol,
            strategy_id=intent.strategy_id,
            side=intent.side,
            size=Quantity(qty),
            entry_price=Price(fill_price),
            open_time=now,
            initial_sl_price=intent.sl_price,
            current_sl_price=intent.sl_price,
            trailing_mode=intent.metadata.get("trailing_mode"),
            trailing_params=intent.metadata.get("trailing_params", {}),
            tp_levels=intent.tp_levels,
        )
        time_stop_bars = intent.metadata.get("time_stop_bars")
        bar_seconds = int(intent.metadata.get("time_stop_bar_seconds", 300))
        if time_stop_bars:
            position.time_stop_at = now + timedelta(seconds=bar_seconds * int(time_stop_bars))
        position.metadata.setdefault("tp_state", {"tp1": False, "tp2": False})
        self.positions[intent.symbol] = position
        intent.status = EntryIntentStatus.FILLED
        return ExecutionReport(
            event=ExecutionEventType.ENTRY_FILLED,
            symbol=intent.symbol,
            side=intent.side,
            quantity=qty,
            price=fill_price,
            timestamp=now,
            intent_id=intent.intent_id,
        )

    def _evaluate_position_targets(
        self,
        position: PositionState,
        mkt: MarketState,
        now: datetime,
    ) -> list[ExecutionReport]:
        reports: list[ExecutionReport] = []
        last_price = float(mkt.mid_price)
        tp_state = position.metadata.setdefault("tp_state", {"tp1": False, "tp2": False})
        r_value = position.risk_per_unit()
        if r_value <= 0:
            r_value = max(abs(last_price * 0.005), 1e-6)
        targets = self._tp_prices(position, r_value)
        if not tp_state["tp1"] and self._hit_target(position.side, last_price, targets[0]):
            reports.append(self._close_fraction(position, 0.5, last_price, now, ExecutionEventType.TAKE_PROFIT, "tp1"))
            tp_state["tp1"] = True
        if not tp_state["tp2"] and self._hit_target(position.side, last_price, targets[1]):
            reports.append(self._close_fraction(position, 0.25, last_price, now, ExecutionEventType.TAKE_PROFIT, "tp2"))
            tp_state["tp2"] = True
        # Trailing portion exits via trailing SL or manual stop.
        reports.extend(self._check_stops(position, last_price, now))
        return [r for r in reports if r is not None]

    def _check_stops(
        self,
        position: PositionState,
        last_price: float,
        now: datetime,
    ) -> list[ExecutionReport]:
        reports: list[ExecutionReport] = []
        trigger = float(position.current_sl_price)
        stop_hit = self._hit_stop(position.side, last_price, trigger)
        if stop_hit:
            reports.append(
                self._close_fraction(position, 1.0, last_price, now, ExecutionEventType.STOP_LOSS, "stop_loss")
            )
            return reports
        if position.time_stop_at and now >= position.time_stop_at:
            reports.append(
                self._close_fraction(position, 1.0, last_price, now, ExecutionEventType.TIME_STOP, "time_stop")
            )
        return reports

    def _close_fraction(
        self,
        position: PositionState,
        fraction: float,
        price: float,
        now: datetime,
        event: ExecutionEventType,
        reason: str,
    ) -> ExecutionReport:
        if position.remaining_size() <= 1e-8:
            return ExecutionReport(
                event=event,
                symbol=position.symbol,
                side=position.side,
                quantity=0.0,
                price=price,
                timestamp=now,
                reason=f"{reason}_noop",
            )
        qty = position.reduce(min(1.0, fraction))
        pnl = self._calc_pnl(position.side, float(position.entry_price), price, qty)
        position.realized_pnl += pnl
        if self.pnl_callback and abs(pnl) > 0.0:
            self.pnl_callback(pnl, now)
        if position.remaining_size() <= 1e-8:
            final_pnl = position.realized_pnl
            self.positions.pop(position.symbol, None)
        return ExecutionReport(
            event=event,
            symbol=position.symbol,
            side=position.side,
            quantity=qty,
            price=price,
            timestamp=now,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Entry logic
    # ------------------------------------------------------------------
    def _execute_market_intent(self, intent: EntryIntent, mkt: MarketState | None, decision: RiskDecision) -> None:
        expected = self._estimate_slippage(intent, mkt)
        intent.expected_slippage_bps = expected
        limit = decision.metadata.get("max_slippage_bps", self.limits.max_slippage_bps)
        if limit and expected > limit:
            intent.status = EntryIntentStatus.REJECTED
            return
        order = OrderIntent(
            symbol=intent.symbol,
            side=intent.side,
            order_type=OrderType.MARKET,
            quantity=intent.size,
            time_in_force=TimeInForce.FOK,
            client_order_id=intent.intent_id,
            comment="market_with_cap",
        )
        self._submit_order(intent, order)

    def _track_limit_intent(self, intent: EntryIntent, mkt: MarketState | None) -> None:
        intent.status = EntryIntentStatus.PENDING
        if mkt:
            self._maybe_trigger_limit(intent, mkt, datetime.utcnow())

    def _activate_limit_retests(
        self,
        market_state: Mapping[Symbol, MarketState],
        now: datetime,
    ) -> list[ExecutionReport]:
        reports: list[ExecutionReport] = []
        for intent in list(self.entry_intents.values()):
            if intent.entry_type != "limit_on_retest":
                continue
            mkt = market_state.get(intent.symbol)
            if not mkt:
                continue
            if intent.is_expired(now):
                intent.status = EntryIntentStatus.CANCELLED
                reports.append(
                    ExecutionReport(
                        event=ExecutionEventType.ENTRY_CANCELLED,
                        symbol=intent.symbol,
                        side=intent.side,
                        quantity=float(intent.size),
                        price=float(intent.entry_price),
                        timestamp=now,
                        intent_id=intent.intent_id,
                        reason="limit_on_retest_ttl",
                    )
                )
                self.entry_intents.pop(intent.intent_id, None)
                continue
            self._maybe_trigger_limit(intent, mkt, now)
        return reports

    def _maybe_trigger_limit(self, intent: EntryIntent, mkt: MarketState, now: datetime) -> None:
        price = float(mkt.mid_price)
        entry_price = float(intent.entry_price)
        if self._hit_retest(intent.side, price, entry_price):
            intent.status = EntryIntentStatus.ACTIVE
            order = OrderIntent(
                symbol=intent.symbol,
                side=intent.side,
                order_type=OrderType.LIMIT,
                quantity=intent.size,
                price=intent.entry_price,
                time_in_force=TimeInForce.GTC,
                post_only=True,
                client_order_id=intent.intent_id,
                comment="limit_on_retest",
            )
            self._submit_order(intent, order)

    def _submit_order(self, intent: EntryIntent, order: OrderIntent) -> None:
        try:
            active_order = self.gateway.submit_order(order)
        except Exception as exc:  # pragma: no cover - network errors
            intent.status = EntryIntentStatus.REJECTED
            return
        self.active_orders[active_order.order_id] = active_order
        if active_order.status == OrderStatus.FILLED:
            self.handle_order_update(active_order)

    # ------------------------------------------------------------------
    # Utility calculations
    # ------------------------------------------------------------------
    @staticmethod
    def _calc_pnl(side: Side, entry: float, exit: float, qty: float) -> float:
        direction = 1 if side is Side.LONG else -1
        return (exit - entry) * qty * direction

    @staticmethod
    def _hit_target(side: Side, price: float, target: float) -> bool:
        return price >= target if side is Side.LONG else price <= target

    @staticmethod
    def _hit_stop(side: Side, price: float, stop_price: float) -> bool:
        return price <= stop_price if side is Side.LONG else price >= stop_price

    @staticmethod
    def _hit_retest(side: Side, price: float, trigger_price: float) -> bool:
        return price <= trigger_price if side is Side.LONG else price >= trigger_price

    @staticmethod
    def _tp_prices(position: PositionState, r_value: float) -> tuple[float, float]:
        entry = float(position.entry_price)
        direction = 1 if position.side is Side.LONG else -1
        tp1 = entry + direction * r_value
        tp2 = entry + direction * 2 * r_value
        return tp1, tp2

    def _estimate_slippage(self, intent: EntryIntent, mkt: MarketState | None) -> float:
        if not mkt:
            return 0.0
        notional = float(intent.size) * float(intent.entry_price)
        depth = max(mkt.depth_pm1_usd, 1)
        depth_component = (notional / depth) * 10_000
        spread_component = mkt.spread_bps * 0.5
        return spread_component + depth_component


__all__ = ["ExecutionEngine", "OrderGateway"]
