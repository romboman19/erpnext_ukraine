from __future__ import annotations

import hashlib
from typing import Any


def serialized_payload_hash(payload: bytes | bytearray | memoryview | str) -> str:
    """Hash exactly what leaves ERPNext, not the full source document."""
    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    return hashlib.sha256(raw).hexdigest()


def record_export_hash(serializer: Any, layout: Any, record: dict) -> str:
    """Serialize one record with its real layout and hash that payload.

    Providers use this value for ``Ecommerce Item Mapping.last_export_hash``.
    Changes to fields excluded by the layout deliberately do not cause export.
    """
    return serialized_payload_hash(serializer.serialize([record], layout))
