"""Enumerations shared across bot subsystems.

The enums below define core contracts (sides, order types, regimes, strategies)
referenced throughout ARCHITECTURE.md. They live in the core package so that all
other modules can import them without introducing circular dependencies.
"""
from __future__ import annotations

from enum import Enum


class Side(str, Enum):
    """Direction of a trade or position."""

    LONG = "long"
    SHORT = "short"


class OrderType(str, Enum):
    """Supported Bybit order types."""

    MARKET = "market"
    LIMIT = "limit"
    POST_ONLY = "post_only"


class TimeInForce(str, Enum):
    """Execution constraints for orders."""

    GTC = "gtc"  # Good till cancel
    IOC = "ioc"  # Immediate or cancel
    FOK = "fok"  # Fill or kill


class Regime(str, Enum):
    """High-level market regimes used by strategies and filters."""

    TREND = "trend"
    RANGE = "range"


class TfProfile(str, Enum):
    """Timeframe profile selection based on liquidity/volatility context."""

    AGGR = "aggr"
    BAL = "bal"
    CONS = "cons"


class EntryType(str, Enum):
    """Taxonomy of how a position is initiated."""

    BREAKOUT = "breakout"
    PULLBACK = "pullback"
    REVERSAL = "reversal"


class StrategyId(str, Enum):
    """Identifiers for the predefined strategy set A–E."""

    STRATEGY_A = "strategy_a_trend_continuation"
    STRATEGY_B = "strategy_b_bb_squeeze"
    STRATEGY_C = "strategy_c_range_break"
    STRATEGY_D = "strategy_d_vwap_mean_reversion"
    STRATEGY_E = "strategy_e_liquidity_sweep"
    STRATEGY_DEBUG_ALWAYS_LONG = "strategy_debug_always_long"
