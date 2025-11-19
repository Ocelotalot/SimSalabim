"""Utilities for dealing with timezones and timestamps.

Time alignment is central to candle aggregation, TF profile selection and
telemetry. The helpers below provide a single source of truth for obtaining
aware datetimes and converting them to numeric timestamps.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .types import Timestamp

DEFAULT_TZ_NAME = "UTC"


def get_app_timezone(tz_name: str | None = None) -> ZoneInfo:
    """Return the ZoneInfo object for the configured timezone."""

    target_name = tz_name or DEFAULT_TZ_NAME
    return ZoneInfo(target_name)


def now_utc() -> datetime:
    """Return the current UTC datetime with tzinfo."""

    return datetime.now(timezone.utc)


def now_in_timezone(tz_name: str | None = None) -> datetime:
    """Return the current datetime localized to the provided timezone."""

    tz = get_app_timezone(tz_name)
    return datetime.now(tz)


def to_unix_timestamp(dt: datetime) -> Timestamp:
    """Convert an aware datetime to a UNIX timestamp (float seconds)."""

    if dt.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware before conversion")
    return Timestamp(dt.timestamp())
