from __future__ import annotations

import pytest

from app.config.models import SymbolGroup
from app.core.enums import Regime, TfProfile
from app.core.types import Symbol, Timestamp
from app.data_feed.candles import Timeframe
from app.market.market_state_builder import MarketStateBuilder
from app.market.models import Candle, OrderBookLevel, OrderBookSnapshot, TradeTick
from app.market.regime_classifier import RegimeClassifier
from app.market.tf_selector import TfProfileSelector


def _make_candles(symbol: Symbol, timeframe: Timeframe, count: int, start: float) -> list[Candle]:
    candles: list[Candle] = []
    base = Timestamp(1_700_000_000.0)
    step = timeframe.seconds
    price = start
    for idx in range(count):
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                start_time=Timestamp(float(base) + idx * step),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + 0.5,
                volume=100 + idx * 2,
            )
        )
        price += 0.5
    return candles


def _make_orderbook(symbol: Symbol) -> OrderBookSnapshot:
    bids = [
        OrderBookLevel(price=100.0, size=60),
        OrderBookLevel(price=99.5, size=40),
        OrderBookLevel(price=98.0, size=30),
    ]
    asks = [
        OrderBookLevel(price=100.5, size=55),
        OrderBookLevel(price=101.0, size=35),
        OrderBookLevel(price=101.5, size=25),
    ]
    return OrderBookSnapshot(symbol=symbol, timestamp_ms=1_700_000_000_000, bids=bids, asks=asks)


def _make_trades(symbol: Symbol, *, now_ms: int, volume_multiplier: float) -> list[TradeTick]:
    trades = [
        TradeTick(symbol=symbol, price=100.2, size=5 * volume_multiplier, is_buyer_maker=False, timestamp_ms=now_ms - 30_000),
        TradeTick(symbol=symbol, price=100.4, size=3 * volume_multiplier, is_buyer_maker=True, timestamp_ms=now_ms - 25_000),
        TradeTick(symbol=symbol, price=100.6, size=2 * volume_multiplier, is_buyer_maker=False, timestamp_ms=now_ms - 120_000),
    ]
    trades.sort(key=lambda trade: trade.timestamp_ms)
    return trades


def test_market_state_builder_should_compute_all_metrics() -> None:
    symbol = Symbol("BTCUSDT")
    builder = MarketStateBuilder(
        RegimeClassifier(hysteresis_bars=1),
        TfProfileSelector(hits_up=1, hits_down=1, cooldown_bars=1),
        atr_period=3,
        atr_quantile_window=4,
        volume_history_size=3,
        vwap_window=5,
        slippage_ema_period=2,
        timezone_name="UTC",
    )
    now_ts = Timestamp(1_700_000_100.0)
    later_ts = Timestamp(float(now_ts) + 300)
    orderbook = _make_orderbook(symbol)
    candles_by_tf = {
        Timeframe.MIN_5: _make_candles(symbol, Timeframe.MIN_5, count=5, start=100.0),
        Timeframe.MIN_15: _make_candles(symbol, Timeframe.MIN_15, count=5, start=100.0),
        Timeframe.MIN_3: _make_candles(symbol, Timeframe.MIN_3, count=6, start=100.0),
    }
    now_ms = int(float(now_ts) * 1_000)

    builder.build_state(
        symbol=symbol,
        group=SymbolGroup.CORE,
        timestamp=now_ts,
        candles_by_tf=candles_by_tf,
        orderbook=orderbook,
        trades=_make_trades(symbol, now_ms=now_ms, volume_multiplier=1.0),
        open_interest_value=1_000_000,
        latency_ms=80,
        latest_slippage_bps=2.0,
    )

    now_ms_later = int(float(later_ts) * 1_000)
    state = builder.build_state(
        symbol=symbol,
        group=SymbolGroup.CORE,
        timestamp=later_ts,
        candles_by_tf=candles_by_tf,
        orderbook=orderbook,
        trades=_make_trades(symbol, now_ms=now_ms_later, volume_multiplier=2.0),
        open_interest_value=1_150_000,
        latency_ms=85,
        latest_slippage_bps=3.0,
    )

    assert state.mid_price == pytest.approx(100.25, rel=1e-3)
    assert state.spread_bps == pytest.approx((100.5 - 100.0) / 100.25 * 10_000, rel=1e-3)
    depth_expected = 100.0 * 60 + 99.5 * 40 + 100.5 * 55 + 101.0 * 35
    assert state.depth_pm1_usd == pytest.approx(depth_expected, rel=1e-3)

    volume_now = sum(trade.notional for trade in _make_trades(symbol, now_ms=now_ms_later, volume_multiplier=2.0))
    volume_prev = sum(trade.notional for trade in _make_trades(symbol, now_ms=now_ms, volume_multiplier=1.0))
    assert state.volume_5m == pytest.approx(volume_now, rel=1e-6)
    assert state.rel_volume_5m == pytest.approx(volume_now / volume_prev, rel=1e-6)

    recent_trades = [
        trade
        for trade in _make_trades(symbol, now_ms=now_ms_later, volume_multiplier=2.0)
        if trade.timestamp_ms >= now_ms_later - 60_000
    ]
    delta_flow = sum(trade.notional if not trade.is_buyer_maker else -trade.notional for trade in recent_trades)
    assert state.delta_flow_1m == pytest.approx(delta_flow, rel=1e-6)

    assert state.ATR_14_5m == pytest.approx(2.0, rel=1e-3)
    assert 0 < state.atr_q_5m <= 1
    assert state.ADX_15m >= 0

    assert len(state.VWAP_window) == 5
    assert state.vwap_mean > 0
    assert state.vwap_slope != 0
    assert state.distance_to_vwap >= 0
    assert state.sigma_vwap >= 0

    assert state.oi_delta_5m > 0
    assert state.avg_slippage_bps == pytest.approx(2 + (2 / 3), rel=1e-3)
    assert state.latency_ms == 85
    assert state.regime in {Regime.RANGE, Regime.TREND}
    assert state.tf_profile in {TfProfile.AGGR, TfProfile.BAL, TfProfile.CONS}
