from __future__ import annotations

from app.core.enums import Regime
from app.core.types import Symbol
from app.market.regime_classifier import RegimeClassifier


def test_regime_classifier_should_switch_with_hysteresis() -> None:
    classifier = RegimeClassifier(hysteresis_bars=2)
    symbol = Symbol("BTCUSDT")

    assert classifier.update(symbol=symbol, adx_15m=10, atr_quantile=0.2, vwap_slope=0.0) is Regime.RANGE

    classifier.update(symbol=symbol, adx_15m=25, atr_quantile=0.8, vwap_slope=0.0002)
    regime = classifier.update(symbol=symbol, adx_15m=25, atr_quantile=0.8, vwap_slope=0.0002)
    assert regime is Regime.TREND

    classifier.update(symbol=symbol, adx_15m=12, atr_quantile=0.2, vwap_slope=0.0)
    regime = classifier.update(symbol=symbol, adx_15m=12, atr_quantile=0.2, vwap_slope=0.0)
    assert regime is Regime.RANGE
