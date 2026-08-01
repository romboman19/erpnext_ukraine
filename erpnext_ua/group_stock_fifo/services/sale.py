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

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class SaleLine:
    """One user-visible line: a prepared allocation and the price it sells at."""

    allocation: str
    rate: Decimal
    uom: str | None = None
    barcode: str | None = None
    discount_amount: Decimal = Decimal("0")


def sell(
    lines: list[SaleLine],
    *,
    customer: str,
    checkout: str,
    posting_date: str | None = None,
    posting_time: str | None = None,
    invoice_values: dict[str, Any] | None = None,
) -> Any:
    """Turn every prepared allocation of one checkout into one checked invoice.

    One invoice rather than one per line, because the customer bought one
    basket: splitting it would give them several receipts for a single purchase
    and make §16.4's comparison meaningless, since all the lines share a lane.
    """
    if not lines:
        raise GSFError("A sale needs at least one line", "MANUAL_REVIEW_REQUIRED")

    allocations = [_prepared_allocation(line.allocation) for line in lines]
    reallocations = [_reallocation_of(allocation) for allocation in allocations]
    stage = _single_stage(reallocations)

    prepared = prepared_stage_value(stage)
    invoice = _submit_invoice(
        allocations,
        lines=lines,
        customer=customer,
        stage=stage,
        checkout=checkout,
        posting_date=posting_date,
        posting_time=posting_time,
        invoice_values=invoice_values or {},
    )
    _assert_cogs_matches(invoice, prepared=prepared)
    for allocation, reallocation in zip(allocations, reallocations):
        _settle_allocation(allocation, reallocation, invoice=invoice, stage=stage)
    # §14.1 ends with "verify zero balance". `release_lane` is that check: it
    # refuses and marks the lane DIRTY if anything is left behind.
    release_lane(reallocations[0].staging_lane, checkout=checkout)
    return invoice


def _prepared_allocation(name: str) -> Any:
    allocation = frappe.get_doc("GSF Allocation", name)
    if allocation.status != ALLOCATION_PREPARED:
        raise GSFError(
            f"Allocation {name} is {allocation.status}, not prepared", "ALLOCATION_CONFLICT"
        )
    return allocation


def _single_stage(reallocations: list) -> str:
    """Every line of one checkout shares one lane, and §9.8 requires it.

    A lane holds exactly one checkout, so lines landing in different lanes would
    mean two checkouts pretending to be one — and the §16.4 comparison, which is
    per lane, could not be made at all.
    """
    lanes = {reallocation.staging_lane for reallocation in reallocations}
    if len(lanes) != 1:
        raise GSFError(
            f"One checkout must prepare into one lane, found {sorted(lanes)}",
            "STAGE_LANE_BUSY",
        )
    stage = frappe.db.get_value("GSF Staging Lane", lanes.pop(), "warehouse")
    if not stage:
        raise GSFError("Staging lane has no warehouse", "WAREHOUSE_BINDING_MISSING")
    return stage


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


def prepared_stage_value(stage: str) -> Decimal:
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
    allocations: list,
    *,
    lines: list[SaleLine],
    customer: str,
    stage: str,
    checkout: str,
    posting_date: str | None,
    posting_time: str | None,
    invoice_values: dict[str, Any],
) -> Any:
    """§18.2: one row per slice, all out of the stage lane, all tagged."""
    company = allocations[0].seller_company
    rows = []
    for allocation, line in zip(allocations, lines):
        discount_percentage = _discount_percentage(allocation, line)
        net_rate = line.rate * (Decimal("100") - discount_percentage) / Decimal("100")
        rows.extend(
            {
                "item_code": allocation.item_code,
                "qty": float(slice_row.qty),
                "rate": float(net_rate),
                "price_list_rate": float(line.rate),
                "discount_percentage": float(discount_percentage),
                "uom": line.uom,
                "barcode": line.barcode,
                "warehouse": stage,
                "income_account": _income_account(company),
                LAYER_FIELD: slice_row.stock_layer,
                ALLOCATION_FIELD: allocation.name,
                SLICE_FIELD: slice_row.name,
                # One user-visible line, so one group across every row it was
                # split into (§18.2, §18.4).
                DISPLAY_GROUP_FIELD: allocation.name,
                "ua_pos_order_item": allocation.external_row_id,
            }
            for slice_row in allocation.slices
        )

    values = {
        "doctype": "Sales Invoice",
        "company": company,
        "customer": customer,
        "update_stock": 1,
        "ignore_pricing_rule": 1,
        "set_posting_time": 1 if posting_date else 0,
        "posting_date": posting_date,
        "posting_time": posting_time,
        MANAGED_SALE_FIELD: 1,
        CHECKOUT_FIELD: checkout,
        "items": rows,
    }
    values.update(_allowed_invoice_values(invoice_values))
    payments = values.pop("payments", None)
    invoice = frappe.get_doc(values)
    invoice.set_missing_values()
    if payments is not None:
        invoice.set("payments", payments)
        invoice.run_method("calculate_taxes_and_totals")
    if invoice.get("ua_pos_order"):
        order = frappe.get_doc("POS Order", invoice.ua_pos_order)
        from erpnext_ua.ua_loyalty.adapters.sales_invoice import prepare_invoice as prepare_loyalty
        from erpnext_ua.ua_gift_certificates.adapters.sales_invoice import prepare_invoice as prepare_gift

        prepare_loyalty(invoice, order)
        prepare_gift(invoice, order)
    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _discount_percentage(allocation: Any, line: SaleLine) -> Decimal:
    qty = sum((Decimal(str(row.qty)) for row in allocation.slices), Decimal("0"))
    gross = qty * line.rate
    if line.discount_amount < 0 or line.discount_amount > gross:
        raise GSFError(
            f"Allocation {allocation.name} has an invalid discount of {line.discount_amount}",
            "MANUAL_REVIEW_REQUIRED",
        )
    return line.discount_amount * Decimal("100") / gross if gross else Decimal("0")


def _allowed_invoice_values(values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "is_pos",
        "ua_pos_order",
        "ua_pos_desk",
        "ua_pos_shift",
        "payments",
        "change_amount",
        "remarks",
    }
    return {key: value for key, value in values.items() if key in allowed}


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


def _settle_allocation(allocation: Any, reallocation: Any, *, invoice: Any, stage: str) -> None:
    """Record the consumption and empty the stage cache for one line."""
    posting = now_datetime()
    for row in allocation.slices:
        invoice_row = _invoice_row_for_slice(invoice, row.name)
        value = _row_cogs(invoice.name, invoice_row.name)
        row.sales_invoice_item = invoice_row.name
        record_movement(
            stock_layer=row.stock_layer,
            movement_type="SALE_CONSUMPTION",
            posting_datetime=posting,
            qty=float(row.qty),
            stock_value=float(value),
            source_company=allocation.seller_company,
            source_warehouse=stage,
            voucher_type="Sales Invoice",
            voucher_no=invoice.name,
            voucher_detail_no=invoice_row.name,
            idempotency_key=f"SALE_CONSUMPTION:{invoice.name}:{row.name}",
        )
        apply_to_balance(
            stock_layer=row.stock_layer,
            company=allocation.seller_company,
            warehouse=stage,
            qty=float(-Decimal(str(row.qty))),
            stock_value=float(-value),
        )

    with service_write():
        reallocation.status = "CONSUMED"
        reallocation.save(ignore_permissions=True)
        allocation.status = ALLOCATION_CONSUMED
        allocation.consumer_doctype = "Sales Invoice"
        allocation.consumer_document = invoice.name
        allocation.save(ignore_permissions=True)


def _invoice_row_for_slice(invoice: Any, slice_name: str) -> Any:
    rows = [row for row in invoice.items if row.get(SLICE_FIELD) == slice_name]
    if len(rows) != 1:
        raise GSFError(
            f"Sale {invoice.name} has {len(rows)} rows for allocation slice {slice_name}",
            "MANUAL_REVIEW_REQUIRED",
        )
    return rows[0]


def _row_cogs(invoice_name: str, invoice_item: str) -> Decimal:
    value = frappe.db.sql(
        """
        select coalesce(sum(stock_value_difference), 0) from `tabStock Ledger Entry`
        where voucher_type = 'Sales Invoice' and voucher_no = %(invoice)s
          and voucher_detail_no = %(invoice_item)s and is_cancelled = 0
        """,
        {"invoice": invoice_name, "invoice_item": invoice_item},
    )[0][0]
    return abs(Decimal(str(value or 0)))


def stage_balance(*, stock_layer: str, company: str, warehouse: str) -> Decimal:
    """What GSF's cache thinks is still staged for one layer."""
    name = balance_identity(stock_layer=stock_layer, company=company, warehouse=warehouse)
    return Decimal(str(frappe.db.get_value("GSF Layer Balance", name, "actual_qty_cache") or 0))
