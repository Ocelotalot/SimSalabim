"""MarketState builder that fuses raw feeds into TZ §4.2 metrics."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Deque, Mapping, Sequence

from zoneinfo import ZoneInfo

from app.config.models import SymbolGroup
from app.core.enums import TfProfile
from app.core.types import Symbol, Timestamp
from app.data_feed.candles import Timeframe

from .indicators import compute_adx, compute_atr, compute_vwap_features, quantile_rank
from .models import Candle, MarketState, OrderBookLevel, OrderBookSnapshot, TradeTick
from .regime_classifier import RegimeClassifier
from .tf_selector import TfProfileSelector, TfSelectionMetrics


@dataclass(slots=True)
class _SymbolHistory:
    """Rolling storage needed to reproduce TZ §4.2.2 statistics."""

    atr_values: Deque[float]
    volume_values: Deque[float]
    open_interest: Deque[tuple[int, float]]
    avg_slippage_bps: float = 0.0


class MarketStateBuilder:
    """Assemble :class:`MarketState` from multi-source inputs (TZ §4.2)."""

    def __init__(
        self,
        regime_classifier: RegimeClassifier,
        tf_selector: TfProfileSelector,
        *,
        atr_period: int = 14,
        atr_quantile_window: int = 288,
        volume_history_size: int = 20,
        vwap_window: int = 20,
        slippage_ema_period: int = 20,
        timezone_name: str = "Europe/Minsk",
    ) -> None:
        self._regime_classifier = regime_classifier
        self._tf_selector = tf_selector
        self._atr_period = atr_period
        self._atr_quantile_window = atr_quantile_window
        self._volume_history_size = volume_history_size
        self._vwap_window = vwap_window
        self._slippage_alpha = 2 / (slippage_ema_period + 1)
        self._tz = ZoneInfo(timezone_name)
        self._history: dict[Symbol, _SymbolHistory] = defaultdict(
            lambda: _SymbolHistory(
                atr_values=deque(maxlen=atr_quantile_window),
                volume_values=deque(maxlen=volume_history_size),
                open_interest=deque(),
            )
        )

    def build_state(
        self,
        *,
        symbol: Symbol,
        group: SymbolGroup,
        timestamp: Timestamp,
        candles_by_tf: Mapping[Timeframe, Sequence[Candle]],
        orderbook: OrderBookSnapshot,
        trades: Sequence[TradeTick],
        open_interest_value: float,
        latency_ms: float,
        latest_slippage_bps: float | None = None,
        price_ref_override: float | None = None,
    ) -> MarketState:
        """Return ``MarketState`` strictly following TZ §4.2.3 field list."""

        history = self._history[symbol]
        now_ms = int(float(timestamp) * 1_000)
        dt_utc = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        dt_local = dt_utc.astimezone(self._tz)

        volume_5m, rel_volume_5m, delta_flow_1m = self._compute_trade_metrics(symbol, trades, now_ms, history)
        atr_5m = self._compute_atr(symbol, candles_by_tf.get(Timeframe.MIN_5, ()))
        adx_15m = compute_adx(candles_by_tf.get(Timeframe.MIN_15, ()), period=self._atr_period)
        oi_delta_5m = self._update_open_interest(history.open_interest, now_ms, open_interest_value)
        avg_slippage_bps = self._update_slippage(symbol, latest_slippage_bps)

        trigger_profile = self._tf_selector.current_profile(symbol)
        trigger_tf = self._profile_to_timeframe(trigger_profile)
        trigger_candles = candles_by_tf.get(trigger_tf, ())
        price_ref = price_ref_override
        if price_ref is None and trigger_candles:
            price_ref = trigger_candles[-1].close
        vwap_window, vwap_slope_raw, vwap_slope, vwap_mean, sigma_vwap, price_ref_for_vwap, distance_to_vwap = (
            compute_vwap_features(trigger_candles, window=self._vwap_window, price_ref=price_ref)
        )

        atr_quantile = self._latest_atr_quantile(symbol)
        regime = self._regime_classifier.update(
            symbol=symbol,
            adx_15m=adx_15m,
            atr_quantile=atr_quantile,
            vwap_slope=vwap_slope,
        )
        tf_profile = self._tf_selector.update(
            symbol=symbol,
            group=group,
            metrics=TfSelectionMetrics(
                atr_quantile=atr_quantile,
                rel_volume=rel_volume_5m,
                spread_bps=self._compute_spread(orderbook),
                depth_pm1_usd=self._compute_depth(orderbook),
                latency_ms=latency_ms,
                avg_slippage_bps=avg_slippage_bps,
            ),
            timestamp=dt_local,
        )

        mid_price = self._compute_mid(orderbook)
        spread_bps = self._compute_spread(orderbook)
        depth_pm1 = self._compute_depth(orderbook)

        return MarketState(
            symbol=symbol,
            group=group,
            timestamp=dt_local,
            mid_price=mid_price,
            spread_bps=spread_bps,
            depth_pm1_usd=depth_pm1,
            volume_5m=volume_5m,
            rel_volume_5m=rel_volume_5m,
            delta_flow_1m=delta_flow_1m,
            ATR_14_5m=atr_5m,
            atr_q_5m=atr_quantile,
            ADX_15m=adx_15m,
            VWAP_window=tuple(vwap_window),
            vwap_slope=vwap_slope,
            vwap_slope_raw=vwap_slope_raw,
            vwap_mean=vwap_mean,
            oi_delta_5m=oi_delta_5m,
            price_ref_for_vwap=price_ref_for_vwap,
            distance_to_vwap=distance_to_vwap,
            sigma_vwap=sigma_vwap,
            avg_slippage_bps=avg_slippage_bps,
            latency_ms=latency_ms,
            regime=regime,
            tf_profile=tf_profile,
        )

    def _compute_mid(self, snapshot: OrderBookSnapshot) -> float:
        if not snapshot.bids or not snapshot.asks:
            return 0.0
        return (snapshot.best_bid + snapshot.best_ask) / 2.0

    def _compute_spread(self, snapshot: OrderBookSnapshot) -> float:
        mid = self._compute_mid(snapshot)
        if mid == 0:
            return 0.0
        return (snapshot.best_ask - snapshot.best_bid) / mid * 10_000.0

    def _compute_depth(self, snapshot: OrderBookSnapshot, pct: float = 0.01) -> float:
        mid = self._compute_mid(snapshot)
        if mid == 0:
            return 0.0
        lower = mid * (1.0 - pct)
        upper = mid * (1.0 + pct)
        return self._depth_within(snapshot.bids, lower, comparison="gte") + self._depth_within(
            snapshot.asks, upper, comparison="lte"
        )

    @staticmethod
    def _depth_within(levels: Sequence[OrderBookLevel], limit_price: float, *, comparison: str) -> float:
        total = 0.0
        for level in levels:
            if comparison == "gte" and level.price < limit_price:
                break
            if comparison == "lte" and level.price > limit_price:
                break
            total += level.price * level.size
        return total

    def _compute_trade_metrics(
        self,
        symbol: Symbol,
        trades: Sequence[TradeTick],
        now_ms: int,
        history: _SymbolHistory,
    ) -> tuple[float, float, float]:
        volume_5m = self._volume_in_window(trades, now_ms, window_sec=300)
        delta_flow_1m = self._delta_flow(trades, now_ms, window_sec=60)
        rel_volume_5m = 0.0
        if history.volume_values:
            base = median(history.volume_values)
            rel_volume_5m = volume_5m / max(base, 1e-9) if base else 0.0
        history.volume_values.append(volume_5m)
        if len(history.volume_values) > self._volume_history_size:
            while len(history.volume_values) > self._volume_history_size:
                history.volume_values.popleft()
        return volume_5m, rel_volume_5m, delta_flow_1m

    @staticmethod
    def _volume_in_window(trades: Sequence[TradeTick], now_ms: int, *, window_sec: int) -> float:
        lower_bound = now_ms - window_sec * 1_000
        total = 0.0
        for trade in reversed(trades):
            if trade.timestamp_ms < lower_bound:
                break
            total += trade.notional
        return total

    @staticmethod
    def _delta_flow(trades: Sequence[TradeTick], now_ms: int, *, window_sec: int) -> float:
        lower_bound = now_ms - window_sec * 1_000
        buy = 0.0
        sell = 0.0
        for trade in reversed(trades):
            if trade.timestamp_ms < lower_bound:
                break
            if trade.taker_side_sign > 0:
                buy += trade.notional
            else:
                sell += trade.notional
        return buy - sell

    def _compute_atr(self, symbol: Symbol, candles: Sequence[Candle]) -> float:
        atr = compute_atr(candles, period=self._atr_period)
        history = self._history[symbol]
        if atr:
            history.atr_values.append(atr)
            if len(history.atr_values) > self._atr_quantile_window:
                while len(history.atr_values) > self._atr_quantile_window:
                    history.atr_values.popleft()
        return atr

    def _latest_atr_quantile(self, symbol: Symbol) -> float:
        history = self._history[symbol]
        if not history.atr_values:
            return 0.0
        current = history.atr_values[-1]
        return quantile_rank(current, history.atr_values)

    def _update_open_interest(
        self,
        history: Deque[tuple[int, float]],
        now_ms: int,
        current_value: float,
        window_ms: int = 5 * 60 * 1_000,
    ) -> float:
        history.append((now_ms, current_value))
        prune_before = now_ms - 2 * window_ms
        while history and history[0][0] < prune_before:
            history.popleft()
        target_ts = now_ms - window_ms
        reference_value: float | None = None
        for ts, value in reversed(history):
            if ts <= target_ts:
                reference_value = value
                break
        if reference_value is None:
            return 0.0
        return current_value - reference_value

    def _update_slippage(self, symbol: Symbol, latest_slippage_bps: float | None) -> float:
        history = self._history[symbol]
        if latest_slippage_bps is None:
            return history.avg_slippage_bps
        if history.avg_slippage_bps == 0.0:
            history.avg_slippage_bps = latest_slippage_bps
        else:
            history.avg_slippage_bps += self._slippage_alpha * (latest_slippage_bps - history.avg_slippage_bps)
        return history.avg_slippage_bps

    def _profile_to_timeframe(self, profile: TfProfile) -> Timeframe:
        if profile == TfProfile.AGGR:
            return Timeframe.MIN_1
        if profile == TfProfile.CONS:
            return Timeframe.MIN_5
        return Timeframe.MIN_3


__all__ = ["MarketStateBuilder"]
