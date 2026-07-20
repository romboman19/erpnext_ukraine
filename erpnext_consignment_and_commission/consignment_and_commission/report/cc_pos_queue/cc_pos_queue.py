from __future__ import annotations

from typing import Any

from ...integrations.reporting import get_pos_queue


def execute(filters: dict[str, Any] | None = None):
    columns = [
        {"fieldname": "creation", "label": "Created", "fieldtype": "Datetime", "width": 145},
        {"fieldname": "checkout", "label": "Checkout", "fieldtype": "Link", "options": "CC POS Checkout", "width": 175},
        {"fieldname": "external_order_name", "label": "External Order", "fieldtype": "Data", "width": 150},
        {"fieldname": "checkout_status", "label": "Checkout Status", "fieldtype": "Data", "width": 120},
        {"fieldname": "payment_state", "label": "Payment", "fieldtype": "Data", "width": 95},
        {"fieldname": "route", "label": "Route", "fieldtype": "Link", "options": "CC POS Route", "width": 175},
        {"fieldname": "fiscal_route", "label": "Fiscal Route", "fieldtype": "Data", "width": 105},
        {"fieldname": "route_status", "label": "Route Status", "fieldtype": "Data", "width": 110},
        {
            "fieldname": "sales_invoice",
            "label": "Sales Invoice",
            "fieldtype": "Link",
            "options": "Sales Invoice",
            "width": 155,
        },
        {
            "fieldname": "print_job",
            "label": "Print Job",
            "fieldtype": "Link",
            "options": "CC POS Print Job",
            "width": 170,
        },
        {"fieldname": "print_status", "label": "Print Status", "fieldtype": "Data", "width": 105},
        {"fieldname": "attempts", "label": "Attempts", "fieldtype": "Int", "width": 75},
        {"fieldname": "last_error", "label": "Last Error", "fieldtype": "Data", "width": 240},
        {"fieldname": "company", "label": "Company", "fieldtype": "Link", "options": "Company", "width": 150},
        {"fieldname": "location", "label": "Location", "fieldtype": "Link", "options": "CC Location", "width": 150},
    ]
    return columns, get_pos_queue(filters)
