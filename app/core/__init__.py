"""Core primitives shared across all subsystems.

This module aggregates enums, common types, utility helpers and error classes
that implement the foundational contracts defined by ARCHITECTURE.md. Higher
level packages import from here to avoid circular dependencies.
"""

from . import enums, errors, time_utils, types

__all__ = ["enums", "errors", "time_utils", "types"]
