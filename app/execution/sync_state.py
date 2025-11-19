"""Helpers for restoring positions from Bybit at startup (TZ §4.7.6)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, MutableMapping, Protocol, Sequence

from app.core.enums import Side, StrategyId
from app.core.types import Price, Quantity, Symbol
from app.risk.models import PositionState

if False:  # pragma: no cover - for typing only
    from app.execution.execution_engine import ExecutionEngine  # noqa: F401


class PositionSnapshot(Mapping[str, object], Protocol):
    """Structural typing for Bybit REST /v5/position responses."""

    def get(self, key: str, default: object | None = None) -> object | None: ...


class PositionFetcher(Protocol):
    """Protocol covering the subset of Bybit client used here."""

    def list_positions(self) -> Sequence[PositionSnapshot]: ...


def snapshot_to_position(snapshot: PositionSnapshot) -> PositionState | None:
    """Convert a raw Bybit snapshot to :class:`PositionState`."""

    size = float(snapshot.get("size", 0))
    if size == 0:
        return None
    symbol = Symbol(str(snapshot.get("symbol")))
    side = Side(str(snapshot.get("side")).lower())
    entry_price = Price(float(snapshot.get("entry_price", snapshot.get("avgPrice", 0))))
    stop_loss = snapshot.get("stop_loss") or snapshot.get("stopLoss")
    sl_price = Price(float(stop_loss)) if stop_loss else Price(entry_price)
    ts_millis = int(snapshot.get("created_time", snapshot.get("createdTime", 0)) or 0)
    open_time = datetime.fromtimestamp(ts_millis / 1000 if ts_millis > 10_000 else ts_millis, tz=timezone.utc)
    strategy_id_raw = snapshot.get("strategy_id") or snapshot.get("tag")
    if strategy_id_raw:
        try:
            strategy_id = StrategyId(strategy_id_raw)
        except ValueError:
            strategy_id = StrategyId.STRATEGY_A
    else:
        strategy_id = StrategyId.STRATEGY_A
    position = PositionState(
        symbol=symbol,
        strategy_id=strategy_id,
        side=side,
        size=Quantity(size),
        entry_price=entry_price,
        open_time=open_time,
        initial_sl_price=sl_price,
        current_sl_price=sl_price,
        trailing_mode=str(snapshot.get("trailing_mode", "none")),
        trailing_params={},
        tp_levels=tuple(),
    )
    return position


def sync_state_from_exchange(client: PositionFetcher) -> dict[Symbol, PositionState]:
    """Fetch open positions from Bybit and return normalized mapping."""

    positions: dict[Symbol, PositionState] = {}
    for snapshot in client.list_positions():
        state = snapshot_to_position(snapshot)
        if state:
            positions[state.symbol] = state
    return positions


def hydrate_execution_engine(
    engine: "ExecutionEngine",
    client: PositionFetcher,
) -> MutableMapping[Symbol, PositionState]:
    """Update :class:`ExecutionEngine` with the latest exchange state."""

    synced = sync_state_from_exchange(client)
    engine.positions.update(synced)
    return engine.positions


__all__ = ["PositionFetcher", "sync_state_from_exchange", "hydrate_execution_engine", "snapshot_to_position"]
