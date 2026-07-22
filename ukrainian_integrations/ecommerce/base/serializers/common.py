from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .transforms import apply_export_transform

MAX_SERIALIZED_RECORDS = 100_000
MAX_SERIALIZED_BYTES = 64 * 1024 * 1024


def value(source: Any, key: str, default=None):
    if isinstance(source, dict):
        return source.get(key, default)
    getter = getattr(source, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(source, key, default)


def layout_fields(layout: Any) -> list[Any]:
    fields = value(layout, "fields", []) or []
    if len(fields) > 500:
        raise ValueError("Ecommerce file layout has too many fields")
    return list(fields)


def encoding(layout: Any) -> str:
    configured = str(value(layout, "encoding", "UTF-8") or "UTF-8").strip()
    aliases = {
        "UTF-8": "utf-8",
        "UTF8": "utf-8",
        "UTF-8-SIG": "utf-8-sig",
        "CP1251": "cp1251",
        "WINDOWS-1251": "cp1251",
    }
    try:
        return aliases[configured.upper()]
    except KeyError as exc:
        raise ValueError(f"Unsupported ecommerce file encoding: {configured}") from exc


def extract(record: dict, path: str):
    current: Any = record
    for part in str(path or "").split("."):
        if not part:
            raise ValueError("ERP field path cannot be empty")
        current = value(current, part)
        if current is None:
            break
    return current


def assign(record: dict, path: str, field_value: Any) -> None:
    parts = str(path or "").split(".")
    if not all(parts):
        raise ValueError("ERP field path cannot be empty")
    current = record
    for part in parts[:-1]:
        current = current.setdefault(part, {})
        if not isinstance(current, dict):
            raise ValueError(f"Cannot assign nested ERP field path: {path}")
    current[parts[-1]] = field_value


def external_row(record: dict, layout: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in layout_fields(layout):
        source_name = str(value(field, "erp_fieldname", "") or "").strip()
        target_name = str(value(field, "external_column", "") or "").strip()
        if not source_name or not target_name:
            raise ValueError("Ecommerce file fields require ERP and external names")
        raw_value = extract(record, source_name)
        if bool(value(field, "required", 0)) and raw_value in (None, ""):
            raise ValueError(f"Required ecommerce export field is empty: {source_name}")
        transformed = apply_export_transform(
            raw_value,
            str(value(field, "transform", "none") or "none"),
            str(value(field, "custom_transform_method", "") or ""),
        )
        result[target_name] = "" if transformed is None else str(transformed)
    return result


def internal_row(record: dict[str, Any], layout: Any) -> dict:
    result: dict = {}
    for field in layout_fields(layout):
        source_name = str(value(field, "erp_fieldname", "") or "").strip()
        target_name = str(value(field, "external_column", "") or "").strip()
        raw_value = record.get(target_name, "")
        if bool(value(field, "required", 0)) and raw_value in (None, ""):
            raise ValueError(f"Required ecommerce import field is empty: {target_name}")
        assign(result, source_name, raw_value)
    return result


def bounded_records(records: Iterable[dict]) -> list[dict]:
    rows = list(records)
    if len(rows) > MAX_SERIALIZED_RECORDS:
        raise ValueError("Ecommerce file contains too many records")
    return rows


def ensure_bounded(payload: bytes) -> bytes:
    if len(payload) > MAX_SERIALIZED_BYTES:
        raise ValueError("Ecommerce file is too large")
    return payload
