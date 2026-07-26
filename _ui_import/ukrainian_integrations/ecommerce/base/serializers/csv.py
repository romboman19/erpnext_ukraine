from __future__ import annotations

import csv
import io
from typing import Any

from .common import (
    bounded_records,
    encoding,
    ensure_bounded,
    external_row,
    internal_row,
    layout_fields,
    value,
)


class CSVSerializer:
    format = "CSV"

    def serialize(self, records: list[dict], layout: Any) -> bytes:
        rows = bounded_records(records)
        delimiter = str(value(layout, "delimiter", ",") or ",")
        if len(delimiter) != 1 or delimiter in {"\r", "\n", "\0"}:
            raise ValueError("CSV delimiter must be one printable character")
        fieldnames = [str(value(field, "external_column", "") or "").strip() for field in layout_fields(layout)]
        if not fieldnames or any(not name for name in fieldnames):
            raise ValueError("CSV layout requires external column names")
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter=delimiter, lineterminator="\n")
        writer.writeheader()
        for record in rows:
            writer.writerow(external_row(record, layout))
        return ensure_bounded(stream.getvalue().encode(encoding(layout)))

    def deserialize(self, payload: bytes | str, layout: Any) -> list[dict]:
        raw = payload.encode(encoding(layout)) if isinstance(payload, str) else bytes(payload)
        ensure_bounded(raw)
        try:
            text = raw.decode(encoding(layout))
        except UnicodeDecodeError as exc:
            raise ValueError("CSV payload does not match its configured encoding") from exc
        delimiter = str(value(layout, "delimiter", ",") or ",")
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows = []
        for index, row in enumerate(reader, start=1):
            if index > 100_000:
                raise ValueError("CSV file contains too many records")
            rows.append(internal_row(dict(row), layout))
        return rows
