"""Utilities for parsing and normalizing Bybit kline data."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Sequence

from app.core.types import Symbol, Timestamp


class Timeframe(str, Enum):
    """Supported Bybit intervals used across MarketState (TZ §4.2)."""

    MIN_1 = "1"
    MIN_3 = "3"
    MIN_5 = "5"
    MIN_15 = "15"
    HOUR_1 = "60"

    @property
    def seconds(self) -> int:
        """Return timeframe duration in seconds."""

        if self is Timeframe.HOUR_1:
            return 60 * 60
        return int(self.value) * 60

    @classmethod
    def from_value(cls, value: str) -> "Timeframe":
        """Map raw interval strings to enum members."""

        for member in cls:
            if member.value == value:
                return member
        raise ValueError(f"Unsupported timeframe: {value}")


@dataclass(slots=True)
class Candle:
    """Normalized OHLCV bar."""

    symbol: Symbol
    timeframe: Timeframe
    start_time: Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float | None = None

    def as_dict(self) -> dict[str, float]:
        """Convenience representation for telemetry/tests."""

        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


def parse_kline_response(symbol: Symbol, timeframe: Timeframe, payload: dict | None) -> List[Candle]:
    """Convert Bybit kline ``result`` payload into :class:`Candle` objects."""

    if not payload:
        return []
    entries = payload.get("list", [])
    candles: List[Candle] = []
    for raw in entries:
        # Bybit returns [startTimeMs, open, high, low, close, volume, turnover]
        start_ms = int(raw[0])
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                start_time=Timestamp(start_ms / 1000.0),
                open=float(raw[1]),
                high=float(raw[2]),
                low=float(raw[3]),
                close=float(raw[4]),
                volume=float(raw[5]),
                turnover=float(raw[6]) if len(raw) > 6 else None,
            )
        )
    candles.sort(key=lambda candle: candle.start_time)
    return candles


def latest_candle(candles: Sequence[Candle]) -> Candle | None:
    """Return the latest candle in chronological order."""

    if not candles:
        return None
    return candles[-1]


def select_by_timeframe(candles: Iterable[Candle], timeframe: Timeframe) -> List[Candle]:
    """Filter candles belonging to ``timeframe``."""

    return [candle for candle in candles if candle.timeframe == timeframe]
