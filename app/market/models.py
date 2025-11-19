"""Domain models describing MarketState inputs and outputs (TZ §4.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from app.config.models import SymbolGroup
from app.core.enums import Regime, TfProfile
from app.core.types import Symbol, Timestamp
from app.data_feed.candles import Timeframe


@dataclass(slots=True)
class Candle:
    """Normalized OHLCV bar copied from the market feed (see TZ §4.2.1)."""

    symbol: Symbol
    timeframe: Timeframe
    start_time: Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class OrderBookLevel:
    """Single bid/ask level with price and contracts (TZ §4.2.2, depth metrics)."""

    price: float
    size: float


@dataclass(slots=True)
class OrderBookSnapshot:
    """Aggregated L2 snapshot required for mid/spread/depth calculations (TZ §4.2.2)."""

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
class TradeTick:
    """Single trade used for volume/flow aggregation (TZ §4.2.2)."""

    symbol: Symbol
    price: float
    size: float
    is_buyer_maker: bool
    timestamp_ms: int

    @property
    def taker_side_sign(self) -> int:
        return -1 if self.is_buyer_maker else 1

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass(slots=True)
class IndicatorState:
    """Container for derived indicator readings (ATR/ADX/VWAP, TZ §4.2.2)."""

    atr_14_5m: float
    atr_q_5m: float
    adx_15m: float
    vwap_window: Sequence[float]
    vwap_slope_raw: float
    vwap_slope: float
    vwap_mean: float
    sigma_vwap: float
    price_ref_for_vwap: float
    distance_to_vwap: float
    oi_delta_5m: float


@dataclass(slots=True)
class MarketState:
    """Snapshot of all metrics consumed by strategies/filters (TZ §4.2.3).

    Strategies A–E inspect ``MarketState`` to validate entry conditions (EMA/VWAP,
    ADX, rel_volume, etc.), while the pre-trade filters (TZ §4.5) enforce
    liquidity/latency/slippage gates using the same structure. The model therefore
    acts as a shared contract between the market subsystem and all signal modules.
    """

    symbol: Symbol
    group: SymbolGroup
    timestamp: datetime
    mid_price: float
    spread_bps: float
    depth_pm1_usd: float
    volume_5m: float
    rel_volume_5m: float
    delta_flow_1m: float
    ATR_14_5m: float
    atr_q_5m: float
    ADX_15m: float
    VWAP_window: tuple[float, ...] = field(default_factory=tuple)
    vwap_slope: float = 0.0
    vwap_slope_raw: float = 0.0
    vwap_mean: float = 0.0
    oi_delta_5m: float = 0.0
    price_ref_for_vwap: float = 0.0
    distance_to_vwap: float = 0.0
    sigma_vwap: float = 0.0
    avg_slippage_bps: float = 0.0
    latency_ms: float = 0.0
    regime: Regime = Regime.RANGE
    tf_profile: TfProfile = TfProfile.BAL

    def indicator_state(self) -> IndicatorState:
        """Return a lightweight :class:`IndicatorState` view (TZ §4.2.2)."""

        return IndicatorState(
            atr_14_5m=self.ATR_14_5m,
            atr_q_5m=self.atr_q_5m,
            adx_15m=self.ADX_15m,
            vwap_window=self.VWAP_window,
            vwap_slope_raw=self.vwap_slope_raw,
            vwap_slope=self.vwap_slope,
            vwap_mean=self.vwap_mean,
            sigma_vwap=self.sigma_vwap,
            price_ref_for_vwap=self.price_ref_for_vwap,
            distance_to_vwap=self.distance_to_vwap,
            oi_delta_5m=self.oi_delta_5m,
        )


__all__ = [
    "Candle",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "TradeTick",
    "IndicatorState",
    "MarketState",
]
