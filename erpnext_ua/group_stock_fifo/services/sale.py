"""The managed sale: one user line, several technical rows (§18, §16.4).

The cashier sees *Item X — 6 units — 1500 each*. ERPNext has to see three rows,
because the six units are three cost layers and gate 0k proved a single row
cannot span two of them — the dimension's negative-stock check rejects it. So
§18.2 is not a reporting preference, it is the shape the platform forces, and
every row carries the slice that justified it plus a shared display group so
§18.4 can put the line back together for printing.

The sale is also the last moment anything can still be undone cheaply. §16.4
requires the COGS ERPNext actually charged to equal the value prepared in the
stage, and requires the transaction to roll back **before** fiscalization if it
does not — after a fiscal receipt exists, the cheapest correction is a legal
return, not a rollback.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import now_datetime

from ..setup.layer_dimension import (
    ALLOCATION_FIELD,
    CHECKOUT_FIELD,
    DISPLAY_GROUP_FIELD,
    LAYER_FIELD,
    MANAGED_SALE_FIELD,
    SLICE_FIELD,
)
from .domain import GSFError, balance_identity
from .layers import apply_to_balance, record_movement
from .reallocation import service_write
from .reservation import ALLOCATION_CONSUMED, ALLOCATION_PREPARED
from .staging import release_lane


def sell(
    allocation_name: str,
    *,
    customer: str,
    rate: Decimal | float | str,
    checkout: str,
    posting_date: str | None = None,
    posting_time: str | None = None,
) -> Any:
    """Turn one prepared allocation into one submitted, checked Sales Invoice."""
    allocation = frappe.get_doc("GSF Allocation", allocation_name)
    if allocation.status != ALLOCATION_PREPARED:
        raise GSFError(
            f"Allocation {allocation_name} is {allocation.status}, not prepared",
            "ALLOCATION_CONFLICT",
        )
    reallocation = _reallocation_of(allocation)
    stage = frappe.db.get_value("GSF Staging Lane", reallocation.staging_lane, "warehouse")

    prepared = prepared_stage_value(allocation, stage)
    invoice = _submit_invoice(
        allocation,
        customer=customer,
        rate=Decimal(str(rate)),
        stage=stage,
        checkout=checkout,
        posting_date=posting_date,
        posting_time=posting_time,
    )
    _assert_cogs_matches(invoice, prepared=prepared)
    _settle(allocation, reallocation, invoice=invoice, stage=stage, checkout=checkout)
    return invoice


def _reallocation_of(allocation: Any) -> Any:
    name = frappe.db.get_value(
        "GSF Stock Reallocation",
        {"allocation": allocation.name, "status": "PREPARED"},
        "name",
    )
    if not name:
        raise GSFError(
            f"Allocation {allocation.name} has no prepared reallocation to sell from",
            "ALLOCATION_CONFLICT",
        )
    return frappe.get_doc("GSF Stock Reallocation", name)


def prepared_stage_value(allocation: Any, stage: str) -> Decimal:
    """§16.4's `prepared_stage_value`, read from the ledger of the stage itself.

    Not from the reallocation's totals: those record what the *sources* issued,
    and the number this has to match is what the stage actually holds.

    The **net** of every row, not the sum of the incoming ones. A lane is reused
    across checkouts, so counting only receipts adds up every preparation the
    lane has ever held — including ones already sold or compensated away.
    """
    value = frappe.db.sql(
        """
        select coalesce(sum(stock_value_difference), 0) from `tabStock Ledger Entry`
        where warehouse = %s and is_cancelled = 0
        """,
        (stage,),
    )[0][0]
    return Decimal(str(value or 0))


def _submit_invoice(
    allocation: Any,
    *,
    customer: str,
    rate: Decimal,
    stage: str,
    checkout: str,
    posting_date: str | None,
    posting_time: str | None,
) -> Any:
    """§18.2: one row per slice, all out of the stage lane, all tagged."""
    company = allocation.seller_company
    invoice = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "company": company,
            "customer": customer,
            "update_stock": 1,
            "set_posting_time": 1 if posting_date else 0,
            "posting_date": posting_date,
            "posting_time": posting_time,
            MANAGED_SALE_FIELD: 1,
            CHECKOUT_FIELD: checkout,
            "items": [
                {
                    "item_code": allocation.item_code,
                    "qty": float(row.qty),
                    "rate": float(rate),
                    "warehouse": stage,
                    "income_account": _income_account(company),
                    LAYER_FIELD: row.stock_layer,
                    ALLOCATION_FIELD: allocation.name,
                    SLICE_FIELD: row.name,
                    # One user-visible line, so one group across every row it
                    # was split into (§18.2, §18.4).
                    DISPLAY_GROUP_FIELD: allocation.name,
                }
                for row in allocation.slices
            ],
        }
    )
    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _income_account(company: str) -> str:
    account = frappe.db.get_value("Company", company, "default_income_account")
    if account:
        return account
    found = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Sales", "is_group": 0}, "name"
    )
    if not found:
        raise GSFError(f"No income account configured for {company}", "MANUAL_REVIEW_REQUIRED")
    return found


def _assert_cogs_matches(invoice: Any, *, prepared: Decimal) -> None:
    """§16.4. Raising here rolls the sale back with everything before it.

    This is the last check that is still cheap. Once a fiscal receipt exists the
    only correction left is a legal return, so a mismatch has to stop the
    transaction rather than be reported afterwards.
    """
    actual = actual_sale_cogs(invoice.name)
    tolerance = Decimal(
        str(frappe.db.get_single_value("GSF Settings", "currency_tolerance") or "0.01")
    )
    if abs(actual - prepared) > tolerance:
        raise GSFError(
            f"Sale {invoice.name} charged {actual} against a prepared stage value of {prepared}; "
            "the stage held stock this sale did not account for",
            "SALE_COGS_MISMATCH",
        )


def actual_sale_cogs(invoice_name: str) -> Decimal:
    """§16.4: `abs(sum(stock_value_difference))` over the sale's own ledger rows."""
    value = frappe.db.sql(
        """
        select coalesce(sum(stock_value_difference), 0) from `tabStock Ledger Entry`
        where voucher_type = 'Sales Invoice' and voucher_no = %s and is_cancelled = 0
        """,
        (invoice_name,),
    )[0][0]
    return abs(Decimal(str(value or 0)))


def _settle(allocation: Any, reallocation: Any, *, invoice: Any, stage: str, checkout: str) -> None:
    """Record the consumption, empty the stage cache, release the lane."""
    posting = now_datetime()
    for row in allocation.slices:
        record_movement(
            stock_layer=row.stock_layer,
            movement_type="SALE_CONSUMPTION",
            posting_datetime=posting,
            qty=float(row.qty),
            stock_value=float(_row_cogs(invoice.name, row.stock_layer)),
            source_company=allocation.seller_company,
            source_warehouse=stage,
            voucher_type="Sales Invoice",
            voucher_no=invoice.name,
            idempotency_key=f"SALE_CONSUMPTION:{invoice.name}:{row.stock_layer}",
        )
        apply_to_balance(
            stock_layer=row.stock_layer,
            company=allocation.seller_company,
            warehouse=stage,
            qty=float(-Decimal(str(row.qty))),
            stock_value=float(-_row_cogs(invoice.name, row.stock_layer)),
        )

    with service_write():
        reallocation.status = "CONSUMED"
        reallocation.save(ignore_permissions=True)
        allocation.status = ALLOCATION_CONSUMED
        allocation.consumer_doctype = "Sales Invoice"
        allocation.consumer_document = invoice.name
        allocation.save(ignore_permissions=True)

    # §14.1 ends with "verify zero balance". `release_lane` is that check: it
    # refuses and marks the lane DIRTY if anything is left behind.
    release_lane(reallocation.staging_lane, checkout=checkout)


def _row_cogs(invoice_name: str, stock_layer: str) -> Decimal:
    value = frappe.db.sql(
        f"""
        select coalesce(sum(stock_value_difference), 0) from `tabStock Ledger Entry`
        where voucher_type = 'Sales Invoice' and voucher_no = %(invoice)s
          and `{LAYER_FIELD}` = %(layer)s and is_cancelled = 0
        """,
        {"invoice": invoice_name, "layer": stock_layer},
    )[0][0]
    return abs(Decimal(str(value or 0)))


def stage_balance(*, stock_layer: str, company: str, warehouse: str) -> Decimal:
    """What GSF's cache thinks is still staged for one layer."""
    name = balance_identity(stock_layer=stock_layer, company=company, warehouse=warehouse)
    return Decimal(str(frappe.db.get_value("GSF Layer Balance", name, "actual_qty_cache") or 0))
