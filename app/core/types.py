"""Shared type aliases for readability and contract enforcement.

The bot frequently passes around strongly-typed primitives (timestamps, prices,
quantities, identifiers). Aliases defined here improve clarity and reduce the
risk of mixing incompatible values across subsystems.
"""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping, NewType, Sequence, TypeAlias

Timestamp = NewType("Timestamp", float)
Symbol = NewType("Symbol", str)
Price = NewType("Price", float)
Quantity = NewType("Quantity", float)
PnlValue = NewType("PnlValue", float)
RiskRatio = NewType("RiskRatio", float)

JSONLike: TypeAlias = Mapping[str, Any]
MutableJSONLike: TypeAlias = MutableMapping[str, Any]
NumericSequence: TypeAlias = Sequence[float]
