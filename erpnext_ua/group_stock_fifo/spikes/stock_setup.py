"""Shared stock plumbing for the Phase 0 gates.

Every document this module creates carries `SPIKE_MARKER` in its remarks, so a
gate can always find and cancel exactly what it made.
"""

from __future__ import annotations

from typing import Any

SPIKE_MARKER = "GSF-SPIKE"
CLEARING_ACCOUNT_NAME = "GSF Group Clearing"
CUSTOMER_NAME = "GSF Phase 0 Покупець"


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


def ensure_customer(frappe: Any) -> str:
    if not frappe.db.exists("Customer", CUSTOMER_NAME):
        frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": CUSTOMER_NAME,
                "customer_type": "Individual",
            }
        ).insert(ignore_permissions=True)
    return CUSTOMER_NAME


def income_account(frappe: Any, company: str) -> str:
    account = frappe.db.get_value("Company", company, "default_income_account")
    if account:
        return account
    found = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Sales", "is_group": 0}, "name"
    )
    if not found:
        raise RuntimeError(f"No income account found for {company}")
    return found


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
    extra: dict[str, Any] | None = None,
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
            **(extra or {}),
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
    extra: dict[str, Any] | None = None,
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
            **(extra or {}),
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
    """Cancel and delete every document this spike suite created.

    Sales Invoices go first because they consume the stock the entries created.
    """
    removed = []
    for doctype in ("Sales Invoice", "Stock Entry"):
        names = frappe.db.sql_list(
            f"select name from `tab{doctype}` where remarks like %s order by creation desc",
            f"{SPIKE_MARKER}%",
        )
        for name in names:
            doc = frappe.get_doc(doctype, name)
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc(doctype, name, force=True, ignore_permissions=True, delete_permanently=True)
            removed.append(f"{doctype} {name}")
    return removed


def purge_orphan_ledger_rows(frappe: Any, item_code: str) -> dict[str, int]:
    """Delete cancelled ledger rows whose parent voucher no longer exists.

    Cancelling a Stock Entry flags its ledger rows instead of removing them, and
    deleting the entry afterwards leaves those rows orphaned. Scoped to one item
    so it can never touch anything outside the spike fixture.
    """
    orphan_vouchers = frappe.db.sql_list(
        """
        select distinct sle.voucher_no
        from `tabStock Ledger Entry` sle
        where sle.item_code = %s
          and sle.is_cancelled = 1
          and sle.voucher_type = 'Stock Entry'
          and not exists (select 1 from `tabStock Entry` se where se.name = sle.voucher_no)
        """,
        item_code,
    )
    if not orphan_vouchers:
        return {"vouchers": [], "stock_ledger_entries": 0, "gl_entries": 0}

    vouchers = tuple(orphan_vouchers)
    sle_count = frappe.db.count("Stock Ledger Entry", {"item_code": item_code, "is_cancelled": 1})
    gl_count = frappe.db.sql(
        """select count(*) from `tabGL Entry`
           where is_cancelled = 1 and voucher_type = 'Stock Entry' and voucher_no in %s""",
        (vouchers,),
    )[0][0]
    frappe.db.sql(
        """delete from `tabStock Ledger Entry`
           where item_code = %s and is_cancelled = 1 and voucher_no in %s""",
        (item_code, vouchers),
    )
    frappe.db.sql(
        """delete from `tabGL Entry`
           where is_cancelled = 1 and voucher_type = 'Stock Entry' and voucher_no in %s""",
        (vouchers,),
    )
    return {"vouchers": orphan_vouchers, "stock_ledger_entries": sle_count, "gl_entries": int(gl_count)}


def active_ledger_rows(frappe: Any, item_code: str) -> set[str]:
    """Names of the live (non-cancelled) stock ledger rows for one item."""
    return set(
        frappe.db.sql_list(
            "select name from `tabStock Ledger Entry` where item_code = %s and is_cancelled = 0",
            item_code,
        )
    )


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
