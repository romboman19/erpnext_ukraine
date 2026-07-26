from __future__ import annotations

from typing import Any

from ...integrations.reconciliation import get_partner_balances


def execute(filters: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns = [
        {
            "fieldname": "company",
            "label": "Company",
            "fieldtype": "Link",
            "options": "Company",
            "width": 180,
        },
        {
            "fieldname": "supplier",
            "label": "Supplier",
            "fieldtype": "Link",
            "options": "Supplier",
            "width": 180,
        },
        {
            "fieldname": "contract",
            "label": "Contract",
            "fieldtype": "Link",
            "options": "CC Contract",
            "width": 180,
        },
        {"fieldname": "relationship_model", "label": "Model", "fieldtype": "Data", "width": 110},
        {
            "fieldname": "currency",
            "label": "Currency",
            "fieldtype": "Link",
            "options": "Currency",
            "width": 90,
        },
        *[
            {
                "fieldname": fieldname,
                "label": label,
                "fieldtype": "Currency",
                "options": "currency",
                "width": 120,
            }
            for fieldname, label in (
                ("unreported_amount", "Unreported"),
                ("gross_reported_amount", "Gross Obligation"),
                ("adjusted_amount", "Return Adjustments"),
                ("reported_amount", "Reported Net"),
                ("paid_amount", "Paid"),
                ("outstanding_amount", "Outstanding"),
                ("partner_credit_amount", "Partner Credit Due"),
                ("overdue_amount", "Overdue"),
            )
        ],
        {
            "fieldname": "unreported_allocations",
            "label": "Unreported Sales",
            "fieldtype": "Int",
            "width": 120,
        },
        {
            "fieldname": "submitted_reports",
            "label": "Settlement Reports",
            "fieldtype": "Int",
            "width": 120,
        },
        {
            "fieldname": "purchase_invoices",
            "label": "Purchase Invoices",
            "fieldtype": "Int",
            "width": 120,
        },
    ]
    return columns, get_partner_balances(filters)
