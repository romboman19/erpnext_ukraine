from __future__ import annotations

from typing import Any

from ...integrations.reporting import get_sale_financials


def execute(filters: dict[str, Any] | None = None):
    columns = [
        {"fieldname": "posting_date", "label": "Date", "fieldtype": "Date", "width": 95},
        {
            "fieldname": "sales_invoice",
            "label": "Sales Invoice",
            "fieldtype": "Link",
            "options": "Sales Invoice",
            "width": 160,
        },
        {"fieldname": "item_code", "label": "Item", "fieldtype": "Link", "options": "Item", "width": 150},
        {"fieldname": "stock_lot", "label": "FIFO Lot", "fieldtype": "Link", "options": "CC Stock Lot", "width": 170},
        {"fieldname": "source_method", "label": "Source", "fieldtype": "Data", "width": 125},
        {"fieldname": "relationship_model", "label": "Model", "fieldtype": "Data", "width": 110},
        {"fieldname": "sold_qty", "label": "Sold Qty", "fieldtype": "Float", "width": 85},
        {"fieldname": "returned_qty", "label": "Returned Qty", "fieldtype": "Float", "width": 100},
        {"fieldname": "currency", "label": "Currency", "fieldtype": "Link", "options": "Currency", "width": 80},
        {
            "fieldname": "net_after_returns",
            "label": "Net Revenue",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 115,
        },
        {
            "fieldname": "partner_after_returns",
            "label": "Partner Debt",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 115,
        },
        {
            "fieldname": "retained_after_returns",
            "label": "Retained Income",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 125,
        },
        {"fieldname": "base_net_after_returns", "label": "Base Revenue", "fieldtype": "Currency", "width": 115},
        {"fieldname": "base_partner_after_returns", "label": "Base Partner", "fieldtype": "Currency", "width": 115},
        {"fieldname": "base_retained_after_returns", "label": "Base Income", "fieldtype": "Currency", "width": 115},
        {"fieldname": "status", "label": "Sale Status", "fieldtype": "Data", "width": 110},
        {
            "fieldname": "settlement_report",
            "label": "Settlement",
            "fieldtype": "Link",
            "options": "CC Settlement Report",
            "width": 170,
        },
        {"fieldname": "settlement_status", "label": "Debt Status", "fieldtype": "Data", "width": 110},
        {"fieldname": "settlement_due_date", "label": "Due Date", "fieldtype": "Date", "width": 95},
        {
            "fieldname": "report_outstanding_amount",
            "label": "Report Outstanding",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 140,
        },
        {"fieldname": "supplier", "label": "Supplier", "fieldtype": "Link", "options": "Supplier", "width": 150},
        {"fieldname": "customer", "label": "Customer", "fieldtype": "Link", "options": "Customer", "width": 150},
        {"fieldname": "company", "label": "Company", "fieldtype": "Link", "options": "Company", "width": 150},
    ]
    return columns, get_sale_financials(filters)
