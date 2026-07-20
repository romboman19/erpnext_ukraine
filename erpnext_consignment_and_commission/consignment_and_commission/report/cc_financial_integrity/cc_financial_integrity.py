from __future__ import annotations

from typing import Any

from ...integrations.reporting import get_financial_integrity_rows


def execute(filters: dict[str, Any] | None = None):
    filters = filters or {}
    columns = [
        {"fieldname": "code", "label": "Code", "fieldtype": "Data", "width": 150},
        {"fieldname": "doctype", "label": "Document Type", "fieldtype": "Data", "width": 160},
        {"fieldname": "name", "label": "Document", "fieldtype": "Dynamic Link", "options": "doctype", "width": 190},
        {"fieldname": "message", "label": "Finding", "fieldtype": "Data", "width": 500},
    ]
    return columns, get_financial_integrity_rows(filters.get("company") or None)
