"""Shared stock plumbing for the Phase 0 gates.

Every document this module creates carries `SPIKE_MARKER` in its remarks, so a
gate can always find and cancel exactly what it made.
"""

from __future__ import annotations

from typing import Any

SPIKE_MARKER = "GSF-SPIKE"
CLEARING_ACCOUNT_NAME = "GSF Group Clearing"


def ensure_clearing_account(frappe: Any, company: str) -> str:
    """Balance-sheet account that carries value between two FOP companies.

    Deliberately an Asset, not an expense: §15 requires the reallocation to
    leave P&L untouched. ERPNext only forbids a Stock-type difference account.
    """
    abbr = frappe.db.get_value("Company", company, "abbr")
    name = f"{CLEARING_ACCOUNT_NAME} - {abbr}"
    if frappe.db.exists("Account", name):
        return name

    parent = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Current Assets", "is_group": 1}, "name"
    ) or frappe.db.get_value("Account", {"company": company, "root_type": "Asset", "is_group": 1}, "name")
    if not parent:
        raise RuntimeError(f"No asset group account found for {company}")

    frappe.get_doc(
        {
            "doctype": "Account",
            "account_name": CLEARING_ACCOUNT_NAME,
            "company": company,
            "parent_account": parent,
            "root_type": "Asset",
            "report_type": "Balance Sheet",
            "is_group": 0,
            "account_currency": frappe.db.get_value("Company", company, "default_currency"),
        }
    ).insert(ignore_permissions=True)
    return name


def receive_layer(
    frappe: Any,
    *,
    company: str,
    warehouse: str,
    item_code: str,
    qty: float,
    rate: float,
    posting_date: str,
    posting_time: str,
    label: str,
) -> str:
    """Material Receipt that seeds one cost layer at an exact rate."""
    return _submit_entry(
        frappe,
        company=company,
        purpose="Material Receipt",
        label=label,
        posting_date=posting_date,
        posting_time=posting_time,
        row={
            "item_code": item_code,
            "qty": qty,
            "t_warehouse": warehouse,
            "basic_rate": rate,
            "set_basic_rate_manually": 1,
            "expense_account": ensure_clearing_account(frappe, company),
        },
    )


def issue_to_clearing(
    frappe: Any,
    *,
    company: str,
    warehouse: str,
    item_code: str,
    qty: float,
    posting_date: str,
    posting_time: str,
    label: str,
) -> str:
    """Material Issue whose counter-entry is the balance-sheet clearing account."""
    return _submit_entry(
        frappe,
        company=company,
        purpose="Material Issue",
        label=label,
        posting_date=posting_date,
        posting_time=posting_time,
        row={
            "item_code": item_code,
            "qty": qty,
            "s_warehouse": warehouse,
            "expense_account": ensure_clearing_account(frappe, company),
        },
    )


def sle_rows(frappe: Any, voucher_no: str) -> list[dict[str, Any]]:
    return frappe.db.sql(
        """
        select warehouse, actual_qty, valuation_rate, stock_value_difference, qty_after_transaction
        from `tabStock Ledger Entry`
        where voucher_no = %s and is_cancelled = 0
        order by creation
        """,
        voucher_no,
        as_dict=True,
    )


def gl_rows(frappe: Any, voucher_no: str) -> list[dict[str, Any]]:
    return frappe.db.sql(
        """
        select account, debit, credit
        from `tabGL Entry`
        where voucher_no = %s and is_cancelled = 0
        order by account
        """,
        voucher_no,
        as_dict=True,
    )


def pnl_total(frappe: Any, voucher_no: str) -> float:
    """Net P&L effect of one voucher. §15 wants this to be exactly zero."""
    value = frappe.db.sql(
        """
        select coalesce(sum(gl.debit - gl.credit), 0)
        from `tabGL Entry` gl
        join `tabAccount` acc on acc.name = gl.account
        where gl.voucher_no = %s and gl.is_cancelled = 0 and acc.report_type = 'Profit and Loss'
        """,
        voucher_no,
    )
    return float(value[0][0]) if value else 0.0


def cancel_spike_entries(frappe: Any) -> list[str]:
    """Cancel and delete every Stock Entry this spike suite created."""
    names = frappe.db.sql_list(
        """
        select name from `tabStock Entry`
        where remarks like %s order by creation desc
        """,
        f"{SPIKE_MARKER}%",
    )
    removed = []
    for name in names:
        doc = frappe.get_doc("Stock Entry", name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc("Stock Entry", name, force=True, ignore_permissions=True, delete_permanently=True)
        removed.append(name)
    return removed


def _submit_entry(
    frappe: Any,
    *,
    company: str,
    purpose: str,
    label: str,
    posting_date: str,
    posting_time: str,
    row: dict[str, Any],
) -> str:
    doc = frappe.get_doc(
        {
            "doctype": "Stock Entry",
            "stock_entry_type": purpose,
            "purpose": purpose,
            "company": company,
            "set_posting_time": 1,
            "posting_date": posting_date,
            "posting_time": posting_time,
            "remarks": f"{SPIKE_MARKER} {label}",
            "items": [row],
        }
    )
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc.name
