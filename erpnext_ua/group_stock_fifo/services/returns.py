"""Customer returns (§19, ADR-009).

Two decisions in §19 look arbitrary until you ask what the alternative costs.

**The return goes back to the company that sold it, not the one that bought it**
(§19.1). The buying company's books closed that stock out when it was reallocated
away; re-opening them would mean a second intercompany movement to undo a sale
that company was never party to.

**The returned units become a new layer dated at the return, not the original
one** (§19.2). Restoring the original FIFO date would insert stock into the
middle of the global queue — behind units already sold, in front of units still
on the shelf — and the local valuation queue would not agree, because ERPNext
puts returned stock where it physically arrived. §17.2's preflight would then
refuse every subsequent sale from that pool. A new layer keeps both orders
saying the same thing.

`return_origin_layer` preserves the link to what was sold, so the trail survives
even though the identity does not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import now_datetime

from ..setup.layer_dimension import LAYER_FIELD, MANAGED_SALE_FIELD
from .domain import (
    LAYER_OPEN,
    LAYER_PENDING,
    RETURN_QUARANTINE_ROLE,
    TRACKING_NONE,
    GSFError,
    LayerOrigin,
    layer_identity,
)
from .layers import apply_to_balance, own_pool, record_movement, tracking_of


@dataclass(frozen=True, slots=True)
class ReturnLine:
    """One returned row, named by the invoice row it came from."""

    sales_invoice_item: str
    qty: Decimal


def accept_return(
    *,
    sales_invoice: str,
    lines: list[ReturnLine],
    posting_date: str | None = None,
    posting_time: str | None = None,
) -> Any:
    """Take stock back into the seller's own pool as a fresh layer (§19.2)."""
    original = frappe.get_doc("Sales Invoice", sales_invoice)
    if not original.get(MANAGED_SALE_FIELD):
        raise GSFError(
            f"{sales_invoice} is not a GSF managed sale", "MANUAL_REVIEW_REQUIRED"
        )
    if original.docstatus != 1:
        raise GSFError(f"{sales_invoice} is not submitted", "MANUAL_REVIEW_REQUIRED")
    if not lines:
        raise GSFError("A return needs at least one line", "MANUAL_REVIEW_REQUIRED")

    sold_rows = {row.name: row for row in original.items}
    credit_note = _draft_credit_note(
        original, lines=lines, sold_rows=sold_rows,
        posting_date=posting_date, posting_time=posting_time,
    )
    layers = _tag_return_layers(original, credit_note)
    credit_note.save(ignore_permissions=True)
    credit_note.submit()
    _open_layers(credit_note, layers)
    return credit_note


def _draft_credit_note(
    original: Any,
    *,
    lines: list[ReturnLine],
    sold_rows: dict[str, Any],
    posting_date: str | None,
    posting_time: str | None,
) -> Any:
    """A standard ERPNext return, routed to the right warehouse per §19.3."""
    rows = []
    for line in lines:
        sold = sold_rows.get(line.sales_invoice_item)
        if not sold:
            raise GSFError(
                f"Row {line.sales_invoice_item} is not part of {original.name}",
                "MANUAL_REVIEW_REQUIRED",
            )
        if Decimal(str(line.qty)) > Decimal(str(sold.qty)):
            raise GSFError(
                f"Cannot return {line.qty} of a row that sold {sold.qty}",
                "MANUAL_REVIEW_REQUIRED",
            )
        rows.append(
            {
                "item_code": sold.item_code,
                # Negative on a return document: ERPNext's own convention, and
                # what makes the credit note reverse the ledger rather than add
                # to it.
                "qty": -float(line.qty),
                "rate": sold.rate,
                "warehouse": _return_warehouse(original.company, sold.item_code),
                "income_account": sold.income_account,
                "sales_invoice_item": sold.name,
            }
        )

    return frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "company": original.company,
            "customer": original.customer,
            "is_return": 1,
            "return_against": original.name,
            "update_stock": 1,
            "set_posting_time": 1 if posting_date else 0,
            "posting_date": posting_date,
            "posting_time": posting_time,
            MANAGED_SALE_FIELD: 1,
            "items": rows,
        }
    ).insert(ignore_permissions=True)


def _return_warehouse(company: str, item_code: str) -> str:
    """§19.3: tracked items go to quarantine, untracked back into the pool.

    A tracked item's identity has to be verified before it rejoins the queue —
    the serial on the box is not evidence that the box holds that serial — and
    §19.3 says exact restore needs its own ADR first.
    """
    if tracking_of(item_code) != TRACKING_NONE:
        quarantine = frappe.db.get_value(
            "GSF Warehouse Binding",
            {
                "company": company,
                "enabled": 1,
                "manager_app": "GSF",
                "warehouse_role": RETURN_QUARANTINE_ROLE,
            },
            "warehouse",
        )
        if not quarantine:
            raise GSFError(
                f"{company} has no GSF return quarantine warehouse; a tracked item "
                "cannot be taken back without one (§19.3)",
                "WAREHOUSE_BINDING_MISSING",
            )
        return quarantine

    pool = frappe.db.get_value(
        "GSF Warehouse Binding",
        {"company": company, "enabled": 1, "manager_app": "GSF", "warehouse_role": "GSF_OWN_POOL"},
        "warehouse",
    )
    if not pool:
        raise GSFError(f"{company} has no GSF own pool to return into", "WAREHOUSE_BINDING_MISSING")
    return pool


def _tag_return_layers(original: Any, credit_note: Any) -> dict[str, str]:
    """One new layer per returned row, linked back to what was sold (§19.2)."""
    sold_layers = {row.name: row.get(LAYER_FIELD) for row in original.items}
    layers: dict[str, str] = {}

    for row in credit_note.items:
        pool = own_pool(row.warehouse)
        if not pool:
            # Quarantine is not an own pool, so no layer is minted: §19.3 keeps
            # tracked returns out of the FIFO queue until someone checks them.
            continue
        origin = LayerOrigin(
            company_group=pool.company_group,
            origin_doctype="Sales Invoice",
            origin_document=credit_note.name,
            origin_row_name=row.name,
            item_code=row.item_code,
        )
        name = layer_identity(origin, site_id=frappe.local.site)
        if not frappe.db.exists("GSF Stock Layer", name):
            frappe.get_doc(
                {
                    "doctype": "GSF Stock Layer",
                    "layer_status": LAYER_PENDING,
                    "company_group": pool.company_group,
                    "physical_location": pool.physical_location,
                    "item_code": row.item_code,
                    "stock_uom": row.stock_uom or row.uom,
                    "origin_company": credit_note.company,
                    "origin_warehouse": row.warehouse,
                    "origin_doctype": "Sales Invoice",
                    "origin_document": credit_note.name,
                    "origin_row_name": row.name,
                    "origin_row_index": row.idx,
                    "original_received_datetime": now_datetime(),
                    "original_received_qty": 0,
                    "tracking_type": TRACKING_NONE,
                    "return_origin_layer": sold_layers.get(row.sales_invoice_item),
                    "created_by_service": "group_stock_fifo.services.returns",
                }
            ).insert(ignore_permissions=True)
        row.set(LAYER_FIELD, name)
        layers[row.name] = name
    return layers


def _open_layers(credit_note: Any, layers: dict[str, str]) -> None:
    """Freeze each return layer from the ledger the credit note actually wrote."""
    for row_name, layer_name in layers.items():
        sle = frappe.db.get_value(
            "Stock Ledger Entry",
            {
                "voucher_type": "Sales Invoice",
                "voucher_no": credit_note.name,
                "voucher_detail_no": row_name,
                "is_cancelled": 0,
            },
            ["name", "warehouse", "actual_qty", "stock_value_difference", "posting_date", "posting_time"],
            as_dict=True,
        )
        if not sle:
            raise GSFError(
                f"Return row {row_name} produced no ledger entry", "SOURCE_VALUE_MISSING"
            )

        layer = frappe.get_doc("GSF Stock Layer", layer_name)
        layer.original_received_qty = abs(sle.actual_qty)
        layer.original_received_datetime = frappe.utils.get_datetime(
            f"{sle.posting_date} {sle.posting_time}"
        )
        layer.origin_warehouse = sle.warehouse
        layer.layer_status = LAYER_OPEN
        layer.save(ignore_permissions=True)

        value = abs(Decimal(str(sle.stock_value_difference or 0)))
        record_movement(
            stock_layer=layer_name,
            movement_type="SALE_RETURN",
            posting_datetime=layer.original_received_datetime,
            qty=abs(sle.actual_qty),
            stock_value=float(value),
            target_company=credit_note.company,
            target_warehouse=sle.warehouse,
            voucher_type="Sales Invoice",
            voucher_no=credit_note.name,
            voucher_detail_no=row_name,
            idempotency_key=f"SALE_RETURN:{credit_note.name}:{row_name}",
        )
        apply_to_balance(
            stock_layer=layer_name,
            company=credit_note.company,
            warehouse=sle.warehouse,
            qty=abs(sle.actual_qty),
            stock_value=float(value),
            last_sle=sle.name,
        )
