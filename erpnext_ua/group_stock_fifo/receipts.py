"""Document hooks for §11 primary receipt and the §17.3 minimisation rules.

Inert unless the feature gate is open and the row actually lands in a GSF OWN
Pool, so an ordinary ERPNext or commission-domain document never notices these
handlers exist.

Why the work is split across `before_submit` and `on_submit`: the layer has to
exist before submit for its dimension to travel into the ledger (gate 0d), and
its quantity, value and FIFO date may only be read after submit, from the ledger
ERPNext actually wrote (gate 0c, ADR-003).
"""

from __future__ import annotations

import frappe

from erpnext_ua.group_stock_fifo.services.domain import (
    LAYER_CANCELLED,
    TRACKING_NONE,
)
from erpnext_ua.group_stock_fifo.services.layers import (
    ORIGIN_RECEIPT,
    OwnPool,
    ReceiptRow,
    apply_to_balance,
    assert_can_receive,
    check_tracking,
    ensure_pending_layer,
    gsf_enabled,
    open_layer,
    own_pool,
    record_movement,
    tracking_of,
)
from erpnext_ua.group_stock_fifo.setup.layer_dimension import (
    INCOMING_LAYER_FIELD,
    LAYER_FIELD,
)


def register_receipt_layers(doc, method=None) -> None:
    """§11.2 before submit: one PENDING layer per managed row, tagged onto the row."""
    for child, row, pool in _managed_rows(doc):
        check_tracking(row, abs(child.get("qty") or 0))
        assert_can_receive(pool, row.company)
        child.set(row.layer_fieldname, ensure_pending_layer(row, pool))


def open_receipt_layers(doc, method=None) -> None:
    """§11.2 after submit: freeze the origin from the ledger and open each layer."""
    for child, row, _pool in _managed_rows(doc):
        layer = child.get(row.layer_fieldname)
        if layer:
            open_layer(layer, row)


def guard_receipt_cancellation(doc, method=None) -> None:
    """§11.4: cancel only while the layer is still untouched, otherwise fail closed."""
    for child, row, _pool in _managed_rows(doc):
        layer = child.get(row.layer_fieldname)
        if not layer:
            continue
        moved = frappe.get_all(
            "GSF Layer Movement",
            filters={"stock_layer": layer, "movement_type": ("!=", ORIGIN_RECEIPT)},
            pluck="name",
            limit=1,
        )
        if moved:
            frappe.throw(
                f"Layer {layer} has already moved; cancelling its receipt needs manual review",
                title="COMPANY_BOUND_STOCK",
            )
        elsewhere = frappe.get_all(
            "GSF Layer Balance",
            filters={
                "stock_layer": layer,
                "warehouse": ("!=", row.warehouse),
                "actual_qty_cache": ("!=", 0),
            },
            pluck="name",
            limit=1,
        )
        if elsewhere:
            frappe.throw(
                f"Layer {layer} holds stock outside {row.warehouse}",
                title="COMPANY_BOUND_STOCK",
            )


def reverse_receipt_layers(doc, method=None) -> None:
    """§11.4 after cancel: reverse the origin movement and close the layer out.

    The layer is cancelled rather than deleted — §34.3 corrects by reversal, and
    a deleted layer would leave its `ORIGIN_RECEIPT` movement pointing nowhere.
    """
    for child, row, _pool in _managed_rows(doc):
        layer = child.get(row.layer_fieldname)
        if not layer:
            continue
        origin = frappe.db.get_value(
            "GSF Layer Movement",
            {"idempotency_key": f"{ORIGIN_RECEIPT}:{layer}"},
            ["name", "qty", "stock_value", "target_warehouse"],
            as_dict=True,
        )
        if not origin:
            continue
        record_movement(
            stock_layer=layer,
            movement_type="REVERSAL",
            posting_datetime=frappe.utils.now_datetime(),
            qty=-origin.qty,
            stock_value=-(origin.stock_value or 0),
            source_company=row.company,
            source_warehouse=origin.target_warehouse,
            voucher_type=row.parent_doctype,
            voucher_no=row.parent_name,
            voucher_detail_no=row.row_name,
            is_reversal=1,
            reversal_of=origin.name,
            idempotency_key=f"REVERSAL:{ORIGIN_RECEIPT}:{layer}",
        )
        apply_to_balance(
            stock_layer=layer,
            company=row.company,
            warehouse=origin.target_warehouse,
            qty=-origin.qty,
            stock_value=-(origin.stock_value or 0),
        )
        frappe.db.set_value("GSF Stock Layer", layer, "layer_status", LAYER_CANCELLED)


def guard_unmanaged_stock_document(doc, method=None) -> None:
    """§17.3: nothing enters a GSF pool except through a GSF flow.

    This is the cheapest of the divergence controls and the one that makes the
    others possible: an untagged unit in an OWN Pool is stock the layer ledger
    cannot account for, and gate 0c showed ERPNext will happily sell it first.
    """
    if not gsf_enabled() or doc.get("gsf_managed"):
        return
    if not frappe.db.get_single_value("GSF Settings", "block_unmanaged_gsf_stock_docs"):
        return
    pools = sorted({wh for wh in _touched_warehouses(doc) if own_pool(wh)})
    if pools:
        frappe.throw(
            f"{doc.doctype} {doc.name} touches GSF-managed {', '.join(pools)} outside a "
            "GSF flow. Stock enters and leaves a GSF pool only through GSF.",
            title="UNCLASSIFIED_GSF_STOCK",
        )


def _touched_warehouses(doc) -> set[str]:
    warehouses: set[str] = set()
    for child in doc.get("items") or []:
        for fieldname in ("warehouse", "s_warehouse", "t_warehouse"):
            value = child.get(fieldname)
            if value:
                warehouses.add(value)
    return warehouses


def _managed_rows(doc):
    """Yield `(child, ReceiptRow, OwnPool)` for rows that bring stock into a pool."""
    if not gsf_enabled():
        return
    for child, warehouse, layer_fieldname in _incoming_rows(doc):
        pool = own_pool(warehouse)
        if not pool:
            continue
        yield child, _receipt_row(doc, child, pool, layer_fieldname), pool


def _incoming_rows(doc):
    """The §11.1 sources, reduced to (row, destination warehouse, dimension field)."""
    if doc.doctype == "Purchase Receipt":
        for child in doc.items:
            yield child, child.warehouse, LAYER_FIELD
    elif doc.doctype == "Purchase Invoice":
        if doc.update_stock:
            for child in doc.items:
                yield child, child.warehouse, LAYER_FIELD
    elif doc.doctype == "Stock Entry":
        # Only a GSF-driven receipt: an unmanaged one is refused outright by
        # `guard_unmanaged_stock_document`, never quietly registered.
        if doc.purpose == "Material Receipt" and doc.get("gsf_managed"):
            for child in doc.items:
                if child.t_warehouse:
                    # On Stock Entry Detail the incoming leg is the `to_` field;
                    # writing the outgoing one instead loses the tag silently.
                    yield child, child.t_warehouse, INCOMING_LAYER_FIELD


def _receipt_row(doc, child, pool: OwnPool, layer_fieldname: str) -> ReceiptRow:
    tracking_type = tracking_of(child.item_code)
    batch_no, serial_numbers = _tracking_identity(child, tracking_type)
    return ReceiptRow(
        parent_doctype=doc.doctype,
        parent_name=doc.name,
        row_name=child.name,
        row_index=child.idx,
        item_code=child.item_code,
        warehouse=pool.warehouse,
        company=doc.company,
        stock_uom=child.get("stock_uom") or child.get("uom"),
        layer_fieldname=layer_fieldname,
        tracking_type=tracking_type,
        batch_no=batch_no,
        serial_numbers=serial_numbers,
    )


def _tracking_identity(child, tracking_type: str) -> tuple[str | None, tuple[str, ...]]:
    """The exact batch or serial identity §11.2 demands, or a refusal."""
    if tracking_type == TRACKING_NONE:
        return None, ()

    bundle = child.get("serial_and_batch_bundle")
    if not bundle:
        serials = tuple(
            line.strip() for line in (child.get("serial_no") or "").splitlines() if line.strip()
        )
        return child.get("batch_no"), serials

    entries = frappe.get_all(
        "Serial and Batch Entry", filters={"parent": bundle}, fields=["batch_no", "serial_no"]
    )
    batches = {entry.batch_no for entry in entries if entry.batch_no}
    if len(batches) > 1:
        # A layer is one identity, and gate 0k proved one document row cannot
        # span two of them. Splitting the row is the operator's call, not ours.
        frappe.throw(
            f"Row {child.idx} receives batches {', '.join(sorted(batches))} at once; "
            "a GSF layer is one batch, so the row has to be split",
            title="BATCH_MISMATCH",
        )
    serials = tuple(entry.serial_no for entry in entries if entry.serial_no)
    return (next(iter(batches), None), serials)
