"""§11 primary receipt registration: the layer registry's write path.

The shape of this service follows from Phase 0 rather than from taste. A layer
is created `PENDING` *before* submit so the row can carry its dimension into the
ledger, and only promoted to `OPEN` *after* submit, once a real
`Stock Ledger Entry` exists to read the quantity and value from — ADR-002 and
ADR-003 both forbid trusting anything GSF computed ahead of the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe.utils import get_datetime, now_datetime

from .domain import (
    LAYER_OPEN,
    LAYER_PENDING,
    OWN_POOL_ROLE,
    TRACKING_BATCH,
    TRACKING_NONE,
    TRACKING_SERIAL,
    GSFError,
    LayerOrigin,
    balance_identity,
    layer_identity,
    validate_tracking_identity,
)

ORIGIN_RECEIPT = "ORIGIN_RECEIPT"
SERVICE_NAME = "group_stock_fifo.services.layers"


def gsf_enabled() -> bool:
    """§44: every hook is inert until the feature gate is deliberately opened."""
    return bool(frappe.db.get_single_value("GSF Settings", "enabled"))


@dataclass(frozen=True, slots=True)
class OwnPool:
    """A warehouse the group owns stock in, with the coordinates it implies."""

    warehouse: str
    company: str
    company_group: str
    physical_location: str


def own_pool(warehouse: str) -> OwnPool | None:
    """The GSF OWN Pool registration for a warehouse, if it is one (§8)."""
    if not warehouse:
        return None
    row = frappe.db.get_value(
        "GSF Warehouse Binding",
        {
            "warehouse": warehouse,
            "enabled": 1,
            "manager_app": "GSF",
            "warehouse_role": OWN_POOL_ROLE,
        },
        ["warehouse", "company", "company_group", "physical_location"],
        as_dict=True,
    )
    if not row:
        return None
    return OwnPool(
        warehouse=row.warehouse,
        company=row.company,
        company_group=row.company_group,
        physical_location=row.physical_location,
    )


def assert_can_receive(pool: OwnPool, company: str) -> None:
    """§11.2: Company, Warehouse and Physical Location must agree before a layer exists."""
    if pool.company != company:
        frappe.throw(
            f"Warehouse {pool.warehouse} belongs to {pool.company}, not {company}",
            title="WAREHOUSE_DOMAIN_CONFLICT",
        )
    if not pool.company_group or not pool.physical_location:
        frappe.throw(
            f"Warehouse binding for {pool.warehouse} names no company group or location",
            title="WAREHOUSE_BINDING_MISSING",
        )
    if frappe.db.get_value("GSF Physical Location", pool.physical_location, "disabled"):
        frappe.throw(f"Location {pool.physical_location} is disabled", title="LOCATION_NOT_ACTIVE")
    binding = frappe.db.exists(
        "GSF Location Company Binding",
        {
            "company_group": pool.company_group,
            "physical_location": pool.physical_location,
            "company": company,
            "enabled": 1,
            "can_purchase": 1,
        },
    )
    if not binding:
        frappe.throw(
            f"{company} has no active purchasing binding in {pool.physical_location}",
            title="COMPANY_NOT_GROUP_MEMBER",
        )


@dataclass(frozen=True, slots=True)
class ReceiptRow:
    """One incoming row, normalised across the three MVP source DocTypes (§11.1)."""

    parent_doctype: str
    parent_name: str
    row_name: str
    row_index: int
    item_code: str
    warehouse: str
    company: str
    stock_uom: str
    layer_fieldname: str
    tracking_type: str = TRACKING_NONE
    batch_no: str | None = None
    serial_numbers: tuple[str, ...] = ()


def ensure_pending_layer(row: ReceiptRow, pool: OwnPool) -> str:
    """Create — or find again — the PENDING layer for a receipt row (§11.2, §11.3).

    Reprocessing the same row must land on the same layer, so the identity is
    the name and the existence check *is* the idempotency check.
    """
    origin = LayerOrigin(
        company_group=pool.company_group,
        origin_doctype=row.parent_doctype,
        origin_document=row.parent_name,
        origin_row_name=row.row_name,
        item_code=row.item_code,
        batch_no=row.batch_no,
        serial_numbers=row.serial_numbers,
    )
    name = layer_identity(origin, site_id=frappe.local.site)
    if frappe.db.exists("GSF Stock Layer", name):
        return name

    frappe.get_doc(
        {
            "doctype": "GSF Stock Layer",
            "layer_status": LAYER_PENDING,
            "company_group": pool.company_group,
            "physical_location": pool.physical_location,
            "item_code": row.item_code,
            "stock_uom": row.stock_uom,
            "origin_company": row.company,
            "origin_warehouse": row.warehouse,
            "origin_doctype": row.parent_doctype,
            "origin_document": row.parent_name,
            "origin_row_name": row.row_name,
            "origin_row_index": row.row_index,
            # A placeholder: the real FIFO date is frozen from the ledger after
            # submit, because that is the date ERPNext will actually order by.
            "original_received_datetime": now_datetime(),
            "original_received_qty": 0,
            "tracking_type": row.tracking_type,
            "batch_no": row.batch_no,
            "serial_numbers": "\n".join(row.serial_numbers),
            "created_by_service": SERVICE_NAME,
        }
    ).insert(ignore_permissions=True)
    return name


def open_layer(layer_name: str, row: ReceiptRow) -> None:
    """§11.2 after submit: freeze the origin from the ledger and open the layer."""
    layer = frappe.get_doc("GSF Stock Layer", layer_name)
    if layer.layer_status == LAYER_OPEN:
        return

    sle = _origin_sle(row)
    layer.original_received_datetime = get_datetime(f"{sle.posting_date} {sle.posting_time}")
    layer.original_received_qty = sle.actual_qty
    layer.origin_warehouse = sle.warehouse
    layer.layer_status = LAYER_OPEN
    layer.save(ignore_permissions=True)

    record_movement(
        stock_layer=layer_name,
        movement_type=ORIGIN_RECEIPT,
        posting_datetime=layer.original_received_datetime,
        qty=sle.actual_qty,
        stock_value=sle.stock_value_difference,
        target_company=row.company,
        target_warehouse=sle.warehouse,
        voucher_type=row.parent_doctype,
        voucher_no=row.parent_name,
        voucher_detail_no=row.row_name,
        idempotency_key=f"{ORIGIN_RECEIPT}:{layer_name}",
    )
    apply_to_balance(
        stock_layer=layer_name,
        company=row.company,
        warehouse=sle.warehouse,
        qty=sle.actual_qty,
        stock_value=sle.stock_value_difference,
        last_sle=sle.name,
    )


def _origin_sle(row: ReceiptRow):
    """The one ledger row this receipt row produced. Absence is fail-closed."""
    entries = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "voucher_type": row.parent_doctype,
            "voucher_no": row.parent_name,
            "voucher_detail_no": row.row_name,
            "is_cancelled": 0,
        },
        fields=[
            "name",
            "warehouse",
            "actual_qty",
            "stock_value_difference",
            "posting_date",
            "posting_time",
        ],
    )
    if len(entries) != 1:
        frappe.throw(
            f"Receipt row {row.row_name} of {row.parent_name} produced {len(entries)} "
            "ledger entries; a layer needs exactly one",
            title="SOURCE_VALUE_MISSING",
        )
    return entries[0]


def record_movement(**values) -> str:
    """Write one §9.11 audit event, or recognise the one already written."""
    existing = frappe.db.exists(
        "GSF Layer Movement", {"idempotency_key": values["idempotency_key"]}
    )
    if existing:
        return existing
    movement = frappe.get_doc({"doctype": "GSF Layer Movement", **values})
    return movement.insert(ignore_permissions=True).name


def apply_to_balance(
    *,
    stock_layer: str,
    company: str,
    warehouse: str,
    qty: float,
    stock_value: float,
    last_sle: str | None = None,
) -> str:
    """Move the §9.10 cache for one layer position.

    A cache, not a source of truth: §9.10 allows it to lag the ledger but never
    to hide a divergence from it, so nothing here recomputes a value — it only
    accumulates what the ledger already reported.
    """
    name = balance_identity(stock_layer=stock_layer, company=company, warehouse=warehouse)
    existing = frappe.db.exists("GSF Layer Balance", name)
    if existing:
        balance = frappe.get_doc("GSF Layer Balance", name)
        balance.actual_qty_cache = (balance.actual_qty_cache or 0) + qty
        balance.stock_value_cache = (balance.stock_value_cache or 0) + stock_value
    else:
        balance = frappe.get_doc(
            {
                "doctype": "GSF Layer Balance",
                "stock_layer": stock_layer,
                "company": company,
                "warehouse": warehouse,
                "physical_location": frappe.db.get_value(
                    "GSF Stock Layer", stock_layer, "physical_location"
                ),
                "warehouse_role": frappe.db.get_value(
                    "GSF Warehouse Binding", {"warehouse": warehouse}, "warehouse_role"
                ),
                "actual_qty_cache": qty,
                "stock_value_cache": stock_value,
            }
        )
    balance.last_sle = last_sle
    balance.last_reconciled_at = now_datetime()
    balance.integrity_status = "OK"
    if existing:
        balance.save(ignore_permissions=True)
    else:
        balance.insert(ignore_permissions=True)
    return name


def tracking_of(item_code: str) -> str:
    """Which identity §11.2 will demand for this item."""
    item = frappe.db.get_value("Item", item_code, ["has_batch_no", "has_serial_no"], as_dict=True)
    if not item:
        return TRACKING_NONE
    if item.has_serial_no:
        return TRACKING_SERIAL
    if item.has_batch_no:
        return TRACKING_BATCH
    return TRACKING_NONE


def check_tracking(row: ReceiptRow, qty: float) -> None:
    try:
        validate_tracking_identity(
            tracking_type=row.tracking_type,
            batch_no=row.batch_no,
            serial_numbers=row.serial_numbers,
            qty=qty,
        )
    except GSFError as error:
        frappe.throw(str(error), title=error.code)
