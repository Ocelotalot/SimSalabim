"""Error hierarchy shared by the bot subsystems.

Centralizing exception types makes it easier for orchestrators to distinguish
between recoverable situations (config reload needed) and fatal issues (bad API
response). Submodules should raise the most specific error available.
"""
from __future__ import annotations


class CoreError(Exception):
    """Base class for all custom exceptions in the application."""


class ConfigurationError(CoreError):
    """Raised when configuration files are missing or invalid."""


class MarketDataError(CoreError):
    """Raised for failures while fetching or parsing market data."""


class StrategyError(CoreError):
    """Raised when strategy logic cannot produce or validate signals."""


class RiskError(CoreError):
    """Raised when risk constraints prevent a requested action."""


class ExecutionError(CoreError):
    """Raised when order submission or tracking fails."""


class RotationError(CoreError):
    """Raised when the rotation subsystem cannot compute symbol scores."""


class RuntimeStateError(CoreError):
    """Raised when runtime JSON files cannot be read or written."""


class TelemetryError(CoreError):
    """Raised for telemetry/logging persistence issues."""
