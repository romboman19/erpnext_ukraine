"""Controlled customer returns for GSF-managed sales (§19, ADR-009/015)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import now_datetime

from ..setup.layer_dimension import (
    ALLOCATION_FIELD,
    DISPLAY_GROUP_FIELD,
    LAYER_FIELD,
    MANAGED_RETURN_FIELD,
    MANAGED_SALE_FIELD,
    RETURN_ORIGIN_LAYER_FIELD,
    SLICE_FIELD,
)
from .domain import (
    LAYER_OPEN,
    LAYER_PENDING,
    RETURN_QUARANTINE_ROLE,
    TRACKING_NONE,
    GSFError,
    LayerOrigin,
    layer_identity,
)
from .layers import apply_to_balance, record_movement, tracking_of
from .pos_return_domain import ReturnLine
from .serial_identity import return_tracking


def accept_return(
    *,
    sales_invoice: str,
    lines: list[ReturnLine],
    posting_date: str | None = None,
    posting_time: str | None = None,
    invoice_values: dict[str, Any] | None = None,
) -> Any:
    """Post a seller-owned return and preserve the exact sold cost lineage."""
    invoice_values = invoice_values or {}
    existing = _existing_external_return(invoice_values)
    if existing:
        return existing

    original = frappe.get_doc("Sales Invoice", sales_invoice)
    _validate_original(original)
    _lock_original(original.name)
    normalized = _normalise_lines(original, lines)
    _assert_returnable(original, normalized)

    credit_note = _draft_credit_note(
        original,
        lines=normalized,
        posting_date=posting_date,
        posting_time=posting_time,
        invoice_values=invoice_values,
    )
    layers = _tag_return_layers(original, credit_note)
    credit_note.save(ignore_permissions=True)
    credit_note.submit()
    _assert_return_values(original, credit_note)
    _open_layers(credit_note, layers)
    return credit_note


def returned_qty(sales_invoice: str, invoice_items: set[str]) -> dict[str, Decimal]:
    """Submitted return quantity by original technical invoice row."""
    totals = {name: Decimal("0") for name in invoice_items}
    if not invoice_items:
        return totals
    returns = frappe.get_all(
        "Sales Invoice",
        filters={"return_against": sales_invoice, "is_return": 1, "docstatus": 1},
        pluck="name",
    )
    if not returns:
        return totals
    rows = frappe.get_all(
        "Sales Invoice Item",
        filters={
            "parent": ("in", returns),
            "sales_invoice_item": ("in", list(invoice_items)),
        },
        fields=["sales_invoice_item", "qty"],
    )
    for row in rows:
        totals[row.sales_invoice_item] += abs(Decimal(str(row.qty or 0)))
    return totals


def _validate_original(original: Any) -> None:
    if not original.get(MANAGED_SALE_FIELD):
        raise GSFError(f"{original.name} is not a GSF managed sale", "MANUAL_REVIEW_REQUIRED")
    if original.docstatus != 1:
        raise GSFError(f"{original.name} is not submitted", "MANUAL_REVIEW_REQUIRED")


def _lock_original(name: str) -> None:
    frappe.db.sql("select name from `tabSales Invoice` where name=%s for update", name)


def _normalise_lines(original: Any, lines: list[ReturnLine]) -> list[ReturnLine]:
    if not lines:
        raise GSFError("A return needs at least one line", "MANUAL_REVIEW_REQUIRED")
    sold_rows = {row.name: row for row in original.items}
    totals: dict[str, Decimal] = {}
    for line in lines:
        qty = Decimal(str(line.qty))
        sold = sold_rows.get(line.sales_invoice_item)
        if not sold:
            raise GSFError(
                f"Row {line.sales_invoice_item} is not part of {original.name}",
                "MANUAL_REVIEW_REQUIRED",
            )
        if qty <= 0:
            raise GSFError("Return quantity must be positive", "MANUAL_REVIEW_REQUIRED")
        if not sold.get(LAYER_FIELD) or not sold.get(ALLOCATION_FIELD) or not sold.get(SLICE_FIELD):
            raise GSFError(
                f"Row {sold.name} has no complete GSF layer/allocation/slice trail",
                "MANUAL_REVIEW_REQUIRED",
            )
        totals[sold.name] = totals.get(sold.name, Decimal("0")) + qty
    return [ReturnLine(name, qty) for name, qty in totals.items()]


def _assert_returnable(original: Any, lines: list[ReturnLine]) -> None:
    sold_rows = {row.name: row for row in original.items}
    prior = returned_qty(original.name, set(sold_rows))
    for line in lines:
        sold_qty = abs(Decimal(str(sold_rows[line.sales_invoice_item].qty or 0)))
        available = sold_qty - prior[line.sales_invoice_item]
        if line.qty > available:
            raise GSFError(
                f"Cannot return {line.qty} from row {line.sales_invoice_item}; only {available} remains",
                "MANUAL_REVIEW_REQUIRED",
            )


def _draft_credit_note(
    original: Any,
    *,
    lines: list[ReturnLine],
    posting_date: str | None,
    posting_time: str | None,
    invoice_values: dict[str, Any],
) -> Any:
    from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

    credit_note = make_sales_return(original.name)
    mapped = {row.sales_invoice_item: row for row in credit_note.items}
    selected = []
    sold_rows = {row.name: row for row in original.items}
    for line in lines:
        row = mapped.get(line.sales_invoice_item)
        sold = sold_rows[line.sales_invoice_item]
        if not row:
            raise GSFError(
                f"ERPNext did not map return row {line.sales_invoice_item}",
                "MANUAL_REVIEW_REQUIRED",
            )
        row.qty = -float(line.qty)
        row.warehouse = _return_warehouse(
            original.company, sold.item_code, sold.get(LAYER_FIELD)
        )
        row.set(ALLOCATION_FIELD, sold.get(ALLOCATION_FIELD))
        row.set(SLICE_FIELD, sold.get(SLICE_FIELD))
        row.set(DISPLAY_GROUP_FIELD, sold.get(DISPLAY_GROUP_FIELD))
        row.set(RETURN_ORIGIN_LAYER_FIELD, sold.get(LAYER_FIELD))
        row.serial_no = sold.serial_no
        row.batch_no = sold.batch_no
        row.use_serial_batch_fields = int(bool(sold.serial_no or sold.batch_no))
        selected.append(row)

    credit_note.set("items", selected)
    credit_note.update(
        {
            "update_stock": 1,
            "set_posting_time": 1 if posting_date else 0,
            "posting_date": posting_date,
            "posting_time": posting_time,
            MANAGED_RETURN_FIELD: 1,
        }
    )
    credit_note.update(_allowed_invoice_values(invoice_values))
    if "payments" in invoice_values:
        credit_note.set("payments", invoice_values["payments"])
    credit_note.run_method("calculate_taxes_and_totals")
    if credit_note.get("ua_pos_order"):
        order = frappe.get_doc("POS Order", credit_note.ua_pos_order)
        from erpnext_ua.ua_gift_certificates.adapters.sales_invoice import prepare_invoice as prepare_gift
        from erpnext_ua.ua_loyalty.adapters.sales_invoice import prepare_invoice as prepare_loyalty

        prepare_loyalty(credit_note, order)
        prepare_gift(credit_note, order)
    return credit_note.insert(ignore_permissions=True)


def _allowed_invoice_values(values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "is_pos",
        "ua_pos_order",
        "ua_pos_desk",
        "ua_pos_shift",
        "ua_fop_profile",
        "change_amount",
        "remarks",
        "ua_sale_fulfillment",
        "ua_fulfillment_route",
    }
    return {key: value for key, value in values.items() if key in allowed}


def _return_warehouse(company: str, item_code: str, sold_layer: str) -> str:
    role = RETURN_QUARANTINE_ROLE if tracking_of(item_code) != TRACKING_NONE else "GSF_OWN_POOL"
    physical_location = frappe.db.get_value("GSF Stock Layer", sold_layer, "physical_location")
    warehouse = frappe.db.get_value(
        "GSF Warehouse Binding",
        {
            "company": company,
            "physical_location": physical_location,
            "enabled": 1,
            "manager_app": "GSF",
            "warehouse_role": role,
        },
        "warehouse",
    )
    if not warehouse:
        raise GSFError(
            f"{company} has no enabled {role} warehouse for this return",
            "WAREHOUSE_BINDING_MISSING",
        )
    return warehouse


def _tag_return_layers(original: Any, credit_note: Any) -> dict[str, str]:
    sold_rows = {row.name: row for row in original.items}
    layers: dict[str, str] = {}
    for row in credit_note.items:
        sold_layer = row.get(RETURN_ORIGIN_LAYER_FIELD)
        if not sold_layer:
            raise GSFError(f"Return row {row.name} has no origin layer", "MANUAL_REVIEW_REQUIRED")
        pool = _return_scope(row.warehouse, credit_note.company)
        sold = sold_rows[row.sales_invoice_item]
        tracking_type, batch_no, serial_numbers = _return_tracking(sold)
        origin = LayerOrigin(
            company_group=pool.company_group,
            origin_doctype="Sales Invoice",
            origin_document=credit_note.name,
            origin_row_name=row.name,
            item_code=row.item_code,
            batch_no=batch_no,
            serial_numbers=tuple(
                line.strip() for line in (serial_numbers or "").splitlines() if line.strip()
            ),
        )
        name = layer_identity(origin, site_id=frappe.local.site)
        if not frappe.db.exists("GSF Stock Layer", name):
            root = frappe.db.get_value("GSF Stock Layer", sold_layer, "lineage_root_layer") or sold_layer
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
                    "original_received_qty": abs(Decimal(str(row.qty))),
                    "tracking_type": tracking_type,
                    "batch_no": batch_no,
                    "serial_numbers": serial_numbers,
                    "return_origin_layer": sold.get(LAYER_FIELD),
                    "lineage_root_layer": root,
                    "created_by_service": "group_stock_fifo.services.returns",
                }
            ).insert(ignore_permissions=True)
        row.set(LAYER_FIELD, name)
        layers[row.name] = name
    return layers


def _return_scope(warehouse: str, company: str) -> Any:
    pool = frappe.db.get_value(
        "GSF Warehouse Binding",
        {
            "warehouse": warehouse,
            "company": company,
            "enabled": 1,
            "manager_app": "GSF",
            "warehouse_role": ("in", ("GSF_OWN_POOL", RETURN_QUARANTINE_ROLE)),
        },
        ["company_group", "physical_location"],
        as_dict=True,
    )
    if not pool:
        raise GSFError(
            f"Return warehouse {warehouse} is not managed for seller {company}",
            "WAREHOUSE_BINDING_MISSING",
        )
    return pool


def _return_tracking(sold: Any) -> tuple[str, str | None, str | None]:
    return return_tracking(sold.serial_no, sold.batch_no)


def _assert_return_values(original: Any, credit_note: Any) -> None:
    tolerance = Decimal(str(frappe.db.get_single_value("GSF Settings", "currency_tolerance") or "0.01"))
    for row in credit_note.items:
        if not row.get(LAYER_FIELD):
            continue
        source = _single_sle(original.name, row.sales_invoice_item)
        returned = _single_sle(credit_note.name, row.name)
        source_qty = abs(Decimal(str(source.actual_qty or 0)))
        if source_qty <= 0:
            raise GSFError(
                f"Original sale row {row.sales_invoice_item} has no issued quantity",
                "SOURCE_VALUE_MISSING",
            )
        expected = abs(Decimal(str(source.stock_value_difference or 0))) * abs(
            Decimal(str(returned.actual_qty or 0))
        ) / source_qty
        actual = abs(Decimal(str(returned.stock_value_difference or 0)))
        if abs(actual - expected) > tolerance:
            raise GSFError(
                f"Return row {row.name} received value {actual}; expected {expected} from {row.sales_invoice_item}",
                "RETURN_COGS_MISMATCH",
            )


def _single_sle(invoice: str, row: str) -> Any:
    entries = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "voucher_type": "Sales Invoice",
            "voucher_no": invoice,
            "voucher_detail_no": row,
            "is_cancelled": 0,
        },
        fields=["name", "warehouse", "actual_qty", "stock_value_difference", "posting_date", "posting_time"],
    )
    if len(entries) != 1:
        raise GSFError(f"Invoice row {row} produced {len(entries)} ledger entries", "SOURCE_VALUE_MISSING")
    return entries[0]


def _open_layers(credit_note: Any, layers: dict[str, str]) -> None:
    for row_name, layer_name in layers.items():
        sle = _single_sle(credit_note.name, row_name)
        layer = frappe.get_doc("GSF Stock Layer", layer_name)
        layer.original_received_qty = abs(sle.actual_qty)
        layer.original_received_datetime = frappe.utils.get_datetime(f"{sle.posting_date} {sle.posting_time}")
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


def _existing_external_return(values: dict[str, Any]) -> Any | None:
    pos_order = values.get("ua_pos_order")
    if not pos_order or not frappe.db.has_column("Sales Invoice", "ua_pos_order"):
        return None
    name = frappe.db.get_value("Sales Invoice", {"ua_pos_order": pos_order, "docstatus": ("!=", 2)}, "name")
    return frappe.get_doc("Sales Invoice", name) if name else None
