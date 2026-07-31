from __future__ import annotations

import hashlib
import json
from typing import Any

from erpnext_ua.ua_loyalty import CALCULATION_SCHEMA_VERSION

from .money import canonical_decimal


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if hasattr(value, "as_tuple"):
        return canonical_decimal(value)
    return value


def canonical_json(payload: dict) -> str:
    return json.dumps(canonicalize(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def quote_envelope(payload: dict) -> dict:
    return {"calculation_schema_version": CALCULATION_SCHEMA_VERSION, **payload}
