"""Order book normalization helpers and liquidity metrics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from app.core.types import Symbol


@dataclass(slots=True)
class OrderBookLevel:
    """Single price level in USDT-perp order book."""

    price: float
    size: float  # contracts (≈USDT notionals for linear perps)


@dataclass(slots=True)
class OrderBookSnapshot:
    """Full L2 snapshot used to derive MarketState metrics."""

    symbol: Symbol
    timestamp_ms: int
    bids: Sequence[OrderBookLevel]
    asks: Sequence[OrderBookLevel]

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 0.0


@dataclass(slots=True)
class OrderBookMetrics:
    """Liquidity aggregates consumed by MarketState/filters."""

    best_bid: float
    best_ask: float
    mid_price: float
    spread_bps: float
    depth_pm1_usd: float


def parse_orderbook_response(symbol: Symbol, payload: dict | None) -> OrderBookSnapshot:
    """Convert ``/v5/market/orderbook`` result to :class:`OrderBookSnapshot`."""

    bids_raw = payload.get("b", []) if payload else []
    asks_raw = payload.get("a", []) if payload else []
    timestamp_ms = int(payload.get("ts", 0)) if payload else 0
    bids = [OrderBookLevel(price=float(price), size=float(size)) for price, size in bids_raw]
    asks = [OrderBookLevel(price=float(price), size=float(size)) for price, size in asks_raw]
    return OrderBookSnapshot(symbol=symbol, timestamp_ms=timestamp_ms, bids=bids, asks=asks)


def compute_mid_price(snapshot: OrderBookSnapshot) -> float:
    """Return ``mid_price = (best_ask + best_bid) / 2``."""

    if not snapshot.bids or not snapshot.asks:
        return 0.0
    return (snapshot.best_bid + snapshot.best_ask) / 2.0


def compute_spread_bps(snapshot: OrderBookSnapshot) -> float:
    """Return spread expressed in basis points (TZ §4.2.2)."""

    mid = compute_mid_price(snapshot)
    if mid == 0.0:
        return 0.0
    return (snapshot.best_ask - snapshot.best_bid) / mid * 10_000.0


def _depth_within_range(levels: Iterable[OrderBookLevel], limit_price: float, *, comparison: str) -> float:
    total = 0.0
    for level in levels:
        if comparison == "gte" and level.price < limit_price:
            break
        if comparison == "lte" and level.price > limit_price:
            break
        total += level.price * level.size
    return total


def compute_depth_pm1(snapshot: OrderBookSnapshot, pct: float = 0.01) -> float:
    """Compute ``depth_±pct_usd`` (defaults to ±1% as in TZ §4.2.2)."""

    mid = compute_mid_price(snapshot)
    if mid == 0.0:
        return 0.0
    lower = mid * (1.0 - pct)
    upper = mid * (1.0 + pct)
    bid_depth = _depth_within_range(snapshot.bids, lower, comparison="gte")
    ask_depth = _depth_within_range(snapshot.asks, upper, comparison="lte")
    return bid_depth + ask_depth


def build_orderbook_metrics(snapshot: OrderBookSnapshot, pct: float = 0.01) -> OrderBookMetrics:
    """Return the tuple of liquidity metrics used in filters and scoring."""

    mid = compute_mid_price(snapshot)
    spread = compute_spread_bps(snapshot)
    depth = compute_depth_pm1(snapshot, pct=pct)
    return OrderBookMetrics(
        best_bid=snapshot.best_bid,
        best_ask=snapshot.best_ask,
        mid_price=mid,
        spread_bps=spread,
        depth_pm1_usd=depth,
    )
