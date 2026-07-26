from __future__ import annotations

from typing import Any

from ...integrations.reporting import get_fifo_inventory


def execute(filters: dict[str, Any] | None = None):
    columns = [
        {"fieldname": "fifo_position", "label": "FIFO #", "fieldtype": "Int", "width": 70},
        {"fieldname": "item_code", "label": "Item", "fieldtype": "Link", "options": "Item", "width": 150},
        {"fieldname": "name", "label": "Stock Lot", "fieldtype": "Link", "options": "CC Stock Lot", "width": 180},
        {"fieldname": "received_datetime", "label": "Received", "fieldtype": "Datetime", "width": 150},
        {"fieldname": "age_days", "label": "Age (days)", "fieldtype": "Int", "width": 85},
        {"fieldname": "source_method", "label": "Source", "fieldtype": "Data", "width": 130},
        {"fieldname": "relationship_model", "label": "Model", "fieldtype": "Data", "width": 110},
        {"fieldname": "ledger_balance", "label": "Ledger Qty", "fieldtype": "Float", "width": 100},
        {"fieldname": "active_reserved_qty", "label": "Reserved", "fieldtype": "Float", "width": 95},
        {"fieldname": "available_qty", "label": "Available", "fieldtype": "Float", "width": 95},
        {"fieldname": "reservation_variance", "label": "Reserve Variance", "fieldtype": "Float", "width": 120},
        {"fieldname": "lot_status", "label": "Status", "fieldtype": "Data", "width": 90},
        {"fieldname": "warehouse", "label": "Warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 170},
        {
            "fieldname": "partner_profile",
            "label": "Partner",
            "fieldtype": "Link",
            "options": "CC Partner Profile",
            "width": 150,
        },
        {"fieldname": "contract", "label": "Contract", "fieldtype": "Link", "options": "CC Contract", "width": 150},
        {"fieldname": "company", "label": "Company", "fieldtype": "Link", "options": "Company", "width": 150},
        {"fieldname": "location", "label": "Location", "fieldtype": "Link", "options": "CC Location", "width": 150},
        {"fieldname": "tracking_type", "label": "Tracking", "fieldtype": "Data", "width": 85},
        {"fieldname": "batch_no", "label": "Batch", "fieldtype": "Link", "options": "Batch", "width": 120},
        {"fieldname": "blocked_reason", "label": "Blocked Reason", "fieldtype": "Data", "width": 180},
    ]
    return columns, get_fifo_inventory(filters)
