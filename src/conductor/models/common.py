"""Shared value types.

Money is always a decimal string on the wire. It is never a binary float, and
sums are produced with :mod:`decimal` only.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

type JsonValue = Any

_DECIMAL_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _parse_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, float):
        # Accepting a float is only for test convenience; callers should use
        # strings or Decimal. repr() avoids binary noise such as 0.1 -> 0.1000000000000000055.
        parsed = Decimal(repr(value))
    elif isinstance(value, str):
        text = value.strip()
        if not _DECIMAL_RE.match(text):
            raise ValueError("invalid decimal string")
        parsed = Decimal(text)
    else:
        raise ValueError("value is not a decimal")
    if not parsed.is_finite():
        raise ValueError("decimal values must be finite")
    return parsed


Money = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal),
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": r"^-?\d+(\.\d+)?$"}, mode="serialization"),
]
"""A decimal money/rate value serialized as a string (no currency conversion)."""


def quantize_money(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places))


def money_str(value: Decimal, places: str = "0.01") -> str:
    return format(value.quantize(Decimal(places)), "f")


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def isoformat_utc(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def canonical_decimal(value: Decimal) -> str:
    """Stable money formatting for digests (no exponent, trailing zeros kept)."""

    return format(value, "f")


def stable_json(value: JsonValue) -> str:
    """Canonical JSON for hashing plan content.

    Dict keys are sorted recursively, decimals become strings, datetimes become
    UTC ISO strings, and floats are rejected to prevent accidental rounding.
    """

    import json

    def _default(item: object) -> object:
        if isinstance(item, Decimal):
            return canonical_decimal(item)
        if isinstance(item, datetime):
            return isoformat_utc(item)
        if isinstance(item, set):
            return sorted(item)
        raise TypeError(f"cannot serialize {type(item).__name__} in a stable digest")

    def _clean(item: object) -> object:
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("non-finite float in digest")
            raise TypeError("floats are not allowed in stable digests; use decimal strings")
        if isinstance(item, dict):
            return {str(key): _clean(val) for key, val in sorted(item.items())}
        if isinstance(item, list | tuple):
            return [_clean(val) for val in item]
        if isinstance(item, Decimal | datetime | set):
            return _default(item)
        if isinstance(item, bool | int | str) or item is None:
            return item
        raise TypeError(f"cannot serialize {type(item).__name__} in a stable digest")

    return json.dumps(_clean(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def parse_decimal(value: str | Decimal) -> Decimal:
    try:
        return _parse_decimal(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise InvalidOperation(str(exc)) from exc
