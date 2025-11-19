"""Trade feed helpers and derived volume metrics."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, List, Sequence

from app.core.enums import Side
from app.core.types import Symbol


@dataclass(slots=True)
class Trade:
    """Single Bybit trade from ``recent-trade`` endpoint."""

    symbol: Symbol
    price: float
    size: float
    side: Side  # taker side
    timestamp_ms: int

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass(slots=True)
class TradeMetrics:
    """Aggregates mapped to TZ §4.2.2 volume/delta fields."""

    volume_5m: float
    rel_volume_5m: float
    delta_flow_1m: float


def parse_trade_response(symbol: Symbol, payload: dict | None) -> List[Trade]:
    """Convert ``/v5/market/recent-trade`` result into :class:`Trade` objects."""

    if not payload:
        return []
    entries = payload.get("list", [])
    trades: List[Trade] = []
    for item in entries:
        # Structure: {'execPrice': '...', 'execQty': '...', 'isBuyerMaker': False, 'time': '167...'}
        price = float(item.get("execPrice", 0.0))
        size = float(item.get("execQty", 0.0))
        is_buyer_maker = item.get("isBuyerMaker")
        # When buyer is maker => seller aggressor => taker side = Side.SHORT
        taker_side = Side.SHORT if is_buyer_maker else Side.LONG
        trades.append(
            Trade(
                symbol=symbol,
                price=price,
                size=size,
                side=taker_side,
                timestamp_ms=int(item.get("time", 0)),
            )
        )
    trades.sort(key=lambda trade: trade.timestamp_ms)
    return trades


def _volume_in_window(trades: Sequence[Trade], now_ms: int, window_sec: int) -> float:
    lower_bound = now_ms - window_sec * 1000
    total = 0.0
    for trade in reversed(trades):
        if trade.timestamp_ms < lower_bound:
            break
        total += trade.notional
    return total


def compute_volume_metrics(
    trades: Sequence[Trade],
    *,
    now_ms: int,
    history_volumes: Iterable[float] | None = None,
    history_size: int = 20,
) -> TradeMetrics:
    """Return ``volume_5m``, ``rel_volume_5m`` and ``delta_flow_1m``.

    ``rel_volume_5m`` divides the latest 5m volume by the median of the provided
    history (defaults to the last ``history_size`` observations). Callers should
    feed rolling history from MarketState to match TZ §4.2.2.
    """

    volume_5m = _volume_in_window(trades, now_ms, window_sec=300)
    delta_flow_1m = _delta_flow(trades, now_ms, window_sec=60)
    rel_volume = 0.0
    if volume_5m > 0 and history_volumes:
        tail = list(history_volumes)[-history_size:]
        if tail:
            rel_volume = volume_5m / max(median(tail), 1e-9)
    return TradeMetrics(volume_5m=volume_5m, rel_volume_5m=rel_volume, delta_flow_1m=delta_flow_1m)


def _delta_flow(trades: Sequence[Trade], now_ms: int, window_sec: int) -> float:
    """Return buy-minus-sell taker volume for the given window."""

    lower_bound = now_ms - window_sec * 1000
    buy_volume = 0.0
    sell_volume = 0.0
    for trade in reversed(trades):
        if trade.timestamp_ms < lower_bound:
            break
        if trade.side == Side.LONG:
            buy_volume += trade.notional
        else:
            sell_volume += trade.notional
    return buy_volume - sell_volume
