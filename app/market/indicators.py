"""Indicator utilities backing MarketState metrics (TZ §4.2.2)."""
from __future__ import annotations

from statistics import fmean, pstdev
from typing import Sequence

from .models import Candle


def _true_range(current: Candle, prev_close: float) -> float:
    high_low = current.high - current.low
    high_close = abs(current.high - prev_close)
    low_close = abs(current.low - prev_close)
    return max(high_low, high_close, low_close)


def compute_atr(candles: Sequence[Candle], period: int = 14) -> float:
    """Return ATR per TZ §4.2.2 using Wilder smoothing on 5m candles."""

    if len(candles) < period + 1:
        return 0.0
    trs = []
    prev_close = candles[0].close
    for candle in candles[1:]:
        tr = _true_range(candle, prev_close)
        trs.append(tr)
        prev_close = candle.close
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_adx(candles: Sequence[Candle], period: int = 14) -> float:
    """Calculate ADX using standard DM+/DM- smoothing (TZ §4.3)."""

    if len(candles) < period + 1:
        return 0.0
    dm_plus_list: list[float] = []
    dm_minus_list: list[float] = []
    tr_list: list[float] = []
    prev = candles[0]
    for candle in candles[1:]:
        up_move = candle.high - prev.high
        down_move = prev.low - candle.low
        dm_plus = up_move if up_move > down_move and up_move > 0 else 0.0
        dm_minus = down_move if down_move > up_move and down_move > 0 else 0.0
        dm_plus_list.append(dm_plus)
        dm_minus_list.append(dm_minus)
        tr_list.append(_true_range(candle, prev.close))
        prev = candle
    if len(tr_list) < period:
        return 0.0
    smoothed_dm_plus = sum(dm_plus_list[:period])
    smoothed_dm_minus = sum(dm_minus_list[:period])
    smoothed_tr = sum(tr_list[:period])
    dx_values: list[float] = []
    for idx in range(period, len(tr_list)):
        smoothed_dm_plus = smoothed_dm_plus - (smoothed_dm_plus / period) + dm_plus_list[idx]
        smoothed_dm_minus = smoothed_dm_minus - (smoothed_dm_minus / period) + dm_minus_list[idx]
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[idx]
        if smoothed_tr == 0:
            continue
        di_plus = 100 * (smoothed_dm_plus / smoothed_tr)
        di_minus = 100 * (smoothed_dm_minus / smoothed_tr)
        dx = 100 * abs(di_plus - di_minus) / max(di_plus + di_minus, 1e-9)
        dx_values.append(dx)
    if not dx_values:
        return 0.0
    return sum(dx_values) / len(dx_values)


def compute_vwap_window(candles: Sequence[Candle], window: int = 20) -> list[float]:
    """Return VWAP per bar for the last ``window`` candles (TZ §4.2.2)."""

    if not candles:
        return []
    selected = list(candles)[-window:]
    vwap_values: list[float] = []
    cum_pv = 0.0
    cum_volume = 0.0
    for candle in selected:
        typical_price = (candle.high + candle.low + candle.close) / 3.0
        volume = max(candle.volume, 1e-9)
        cum_pv += typical_price * volume
        cum_volume += volume
        vwap_values.append(cum_pv / cum_volume)
    return vwap_values


def linear_regression_slope(series: Sequence[float]) -> float:
    """Return slope of y over x=0..n-1 used for VWAP trend (TZ §4.2.2)."""

    n = len(series)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = fmean(series)
    numerator = sum((idx - x_mean) * (value - y_mean) for idx, value in enumerate(series))
    denominator = sum((idx - x_mean) ** 2 for idx in range(n))
    if denominator == 0:
        return 0.0
    return numerator / denominator


def compute_vwap_features(
    candles: Sequence[Candle],
    window: int = 20,
    *,
    price_ref: float | None = None,
) -> tuple[list[float], float, float, float, float, float, float]:
    """Return VWAP window, slope_raw, slope_norm, mean, sigma, price_ref, distance.

    The tuple matches the VWAP-related entries inside MarketState (TZ §4.2.2).
    """

    vwap_window = compute_vwap_window(candles, window=window)
    if not vwap_window:
        return ([], 0.0, 0.0, 0.0, 0.0, price_ref or 0.0, 0.0)
    vwap_mean = fmean(vwap_window)
    slope_raw = linear_regression_slope(vwap_window)
    slope_norm = slope_raw / vwap_mean if vwap_mean else 0.0
    price_reference = price_ref if price_ref is not None else candles[-1].close
    distance = abs(price_reference - vwap_window[-1])
    closes = [candle.close for candle in candles[-len(vwap_window):]]
    diffs = [close - vwap for close, vwap in zip(closes, vwap_window)]
    sigma = pstdev(diffs) if len(diffs) > 1 else 0.0
    return (vwap_window, slope_raw, slope_norm, vwap_mean, sigma, price_reference, distance)


def quantile_rank(value: float, samples: Sequence[float]) -> float:
    """Return percentile rank of ``value`` within ``samples`` (TZ §4.2.2)."""

    if not samples:
        return 0.0
    sorted_samples = sorted(samples)
    count = len(sorted_samples)
    less_or_equal = 0
    for sample in sorted_samples:
        if sample <= value:
            less_or_equal += 1
        else:
            break
    return min(max(less_or_equal / count, 0.0), 1.0)


__all__ = [
    "compute_atr",
    "compute_adx",
    "compute_vwap_window",
    "compute_vwap_features",
    "linear_regression_slope",
    "quantile_rank",
]
