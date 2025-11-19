from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config.models import SymbolGroup
from app.core.enums import TfProfile
from app.core.types import Symbol
from app.market.tf_selector import TfProfileSelector, TfSelectionMetrics


def test_tf_selector_should_promote_aggr_when_metrics_strong() -> None:
    selector = TfProfileSelector(hits_up=1, hits_down=1, cooldown_bars=1)
    metrics = TfSelectionMetrics(
        atr_quantile=0.9,
        rel_volume=1.8,
        spread_bps=2.0,
        depth_pm1_usd=5_000_000,
        latency_ms=80,
        avg_slippage_bps=1.0,
    )
    profile = selector.update(
        symbol=Symbol("BTCUSDT"),
        group=SymbolGroup.CORE,
        metrics=metrics,
        timestamp=datetime(2024, 1, 1, 12, tzinfo=ZoneInfo("Europe/Minsk")),
    )
    assert profile is TfProfile.AGGR


def test_tf_selector_should_switch_to_cons_on_latency_and_slippage() -> None:
    selector = TfProfileSelector(hits_up=1, hits_down=1, cooldown_bars=1)
    metrics = TfSelectionMetrics(
        atr_quantile=0.2,
        rel_volume=0.6,
        spread_bps=5.0,
        depth_pm1_usd=500_000,
        latency_ms=220,
        avg_slippage_bps=8.0,
    )
    profile = selector.update(
        symbol=Symbol("ARBUSDT"),
        group=SymbolGroup.PLUS,
        metrics=metrics,
        timestamp=datetime(2024, 1, 1, 15, tzinfo=ZoneInfo("Europe/Minsk")),
    )
    assert profile is TfProfile.CONS


def test_tf_selector_should_apply_night_shift() -> None:
    selector = TfProfileSelector(hits_up=1, hits_down=1, cooldown_bars=1)
    metrics = TfSelectionMetrics(
        atr_quantile=0.9,
        rel_volume=1.6,
        spread_bps=2.0,
        depth_pm1_usd=4_000_000,
        latency_ms=90,
        avg_slippage_bps=1.0,
    )
    symbol = Symbol("ETHUSDT")
    first = selector.update(
        symbol=symbol,
        group=SymbolGroup.CORE,
        metrics=metrics,
        timestamp=datetime(2024, 1, 1, 21, 0, tzinfo=ZoneInfo("Europe/Minsk")),
    )
    assert first is TfProfile.AGGR
    shifted = selector.update(
        symbol=symbol,
        group=SymbolGroup.CORE,
        metrics=metrics,
        timestamp=datetime(2024, 1, 1, 22, 5, tzinfo=ZoneInfo("Europe/Minsk")),
    )
    assert shifted in {TfProfile.BAL, TfProfile.CONS}
