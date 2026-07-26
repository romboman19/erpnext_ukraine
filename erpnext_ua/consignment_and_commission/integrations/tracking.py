"""Immutable ownership mapping for ERPNext Batch and Serial No masters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..services.tracking import TRACKING_BATCH, TRACKING_NONE, TRACKING_SERIAL
from ..setup.ownership_dimension import (
    OWN_RECEIPT_FIELD,
    OWN_RECEIPT_ITEM_FIELD,
    OWNERSHIP_FIELD,
    RECEIPT_FIELD,
    RECEIPT_ITEM_FIELD,
)


@dataclass(frozen=True, slots=True)
class TrackingSelection:
    doctype: str
    name: str


def _split_serial_numbers(value: str | None) -> tuple[str, ...]:
    return tuple(line.strip() for line in (value or "").splitlines() if line.strip())


def _bundle_selections(frappe: Any, bundle_name: str | None) -> list[TrackingSelection]:
    if not bundle_name:
        return []
    selections: list[TrackingSelection] = []
    for entry in frappe.get_all(
        "Serial and Batch Entry",
        filters={"parent": bundle_name},
        fields=["serial_no", "batch_no"],
        order_by="idx asc",
    ):
        if entry.serial_no:
            selections.append(TrackingSelection("Serial No", entry.serial_no))
        if entry.batch_no:
            selections.append(TrackingSelection("Batch", entry.batch_no))
    return selections


def get_tracking_selections(frappe: Any, row: Any) -> tuple[TrackingSelection, ...]:
    selections: list[TrackingSelection] = []
    if row.get("batch_no"):
        selections.append(TrackingSelection("Batch", row.batch_no))
    selections.extend(
        TrackingSelection("Serial No", serial_no)
        for serial_no in _split_serial_numbers(row.get("serial_no"))
    )
    selections.extend(_bundle_selections(frappe, row.get("serial_and_batch_bundle")))

    unique: list[TrackingSelection] = []
    seen: set[tuple[str, str]] = set()
    for selection in selections:
        key = (selection.doctype, selection.name)
        if key not in seen:
            seen.add(key)
            unique.append(selection)
    return tuple(unique)


def _row_ownership_values(document: Any, row: Any) -> tuple[str, ...]:
    values = []
    if document.doctype == "Purchase Invoice" and document.get("update_stock"):
        if row.get("warehouse") and row.get(OWNERSHIP_FIELD):
            values.append(row.get(OWNERSHIP_FIELD))
    if document.doctype == "Sales Invoice" and document.get("update_stock"):
        if row.get("warehouse") and row.get(OWNERSHIP_FIELD):
            values.append(row.get(OWNERSHIP_FIELD))
    if row.get("s_warehouse") and row.get(OWNERSHIP_FIELD):
        values.append(row.get(OWNERSHIP_FIELD))
    if row.get("t_warehouse") and row.get(f"to_{OWNERSHIP_FIELD}"):
        values.append(row.get(f"to_{OWNERSHIP_FIELD}"))
    return tuple(dict.fromkeys(values))


def _is_controlled_receipt_inward(document: Any, row: Any) -> bool:
    stock_entry_inward = bool(
        document.get(RECEIPT_FIELD)
        and row.get(RECEIPT_ITEM_FIELD)
        and row.get("t_warehouse")
        and not row.get("s_warehouse")
    )
    purchase_inward = bool(
        document.doctype == "Purchase Invoice"
        and document.get("update_stock")
        and document.get(OWN_RECEIPT_FIELD)
        and row.get(OWN_RECEIPT_ITEM_FIELD)
        and row.get("warehouse")
    )
    return stock_entry_inward or purchase_inward


def _validate_document_tracking_ownership(document: Any) -> None:
    import frappe

    for row in document.items:
        item_tracking = frappe.get_cached_value(
            "Item",
            row.item_code,
            ["has_batch_no", "has_serial_no"],
            as_dict=True,
        )
        if not item_tracking or not (
            item_tracking.has_batch_no or item_tracking.has_serial_no
        ):
            continue
        selections = get_tracking_selections(frappe, row)
        if row.get("s_warehouse") and row.get("t_warehouse"):
            source_owner = row.get(OWNERSHIP_FIELD)
            target_owner = row.get(f"to_{OWNERSHIP_FIELD}")
            if (source_owner or target_owner) and source_owner != target_owner:
                frappe.throw(
                    f"Row {row.idx}: tracked transfer must preserve the same CC Stock Lot "
                    "on source and target"
                )
        expected_owners = _row_ownership_values(document, row)
        controlled_inward = _is_controlled_receipt_inward(document, row)

        expected_owner = expected_owners[0] if expected_owners else None
        selected_owners = {
            frappe.db.get_value(selection.doctype, selection.name, OWNERSHIP_FIELD)
            for selection in selections
            if frappe.db.exists(selection.doctype, selection.name)
        }
        selected_owners.discard(None)

        if not expected_owner:
            if selected_owners:
                frappe.throw(
                    f"Row {row.idx}: selected Batch/Serial belongs to CC Stock Lot "
                    f"{sorted(selected_owners)[0]}; set the matching ownership dimension"
                )
            continue

        if row.get("s_warehouse") and not selections:
            frappe.throw(
                f"Row {row.idx}: CC tracked stock requires an explicit Batch or Serial No"
            )
        if not selections and not controlled_inward:
            frappe.throw(
                f"Row {row.idx}: CC Stock Lot {expected_owner} requires an explicit Batch or Serial No"
            )

        for selection in selections:
            exists = frappe.db.exists(selection.doctype, selection.name)
            actual_owner = (
                frappe.db.get_value(selection.doctype, selection.name, OWNERSHIP_FIELD)
                if exists
                else None
            )
            if actual_owner == expected_owner:
                continue
            if controlled_inward and not actual_owner:
                continue
            frappe.throw(
                f"Row {row.idx}: {selection.doctype} {selection.name} belongs to "
                f"{actual_owner or 'no CC Stock Lot'}, not {expected_owner}"
            )


def validate_stock_entry_tracking_ownership(document: Any, method: str | None = None) -> None:
    """Reject Stock Entry tracking identities that disagree with the ownership dimension."""
    del method
    _validate_document_tracking_ownership(document)


def validate_purchase_invoice_tracking_ownership(
    document: Any, method: str | None = None
) -> None:
    """Protect controlled OWN receipt Batch/Serial identity before ledger posting."""
    del method
    if document.get("update_stock"):
        _validate_document_tracking_ownership(document)


def validate_sales_invoice_tracking_ownership(
    document: Any, method: str | None = None
) -> None:
    """Protect controlled CC sale Batch/Serial identity before ledger posting."""
    del method
    if document.get("update_stock"):
        _validate_document_tracking_ownership(document)


def validate_tracking_owner_immutability(document: Any, method: str | None = None) -> None:
    """Prevent ordinary Batch/Serial saves from changing an established owner."""
    del method
    import frappe

    if document.is_new() or not frappe.db.has_column(document.doctype, OWNERSHIP_FIELD):
        return
    persisted = frappe.db.get_value(document.doctype, document.name, OWNERSHIP_FIELD)
    requested = document.get(OWNERSHIP_FIELD)
    if str(persisted or "") != str(requested or ""):
        frappe.throw(
            f"{document.doctype} {document.name} CC Stock Lot ownership is immutable"
        )


def guard_owned_tracking_deletion(document: Any, method: str | None = None) -> None:
    """Keep the physical tracking master as immutable ownership audit evidence."""
    del method
    import frappe

    stock_lot = document.get(OWNERSHIP_FIELD) or frappe.db.get_value(
        document.doctype,
        document.name,
        OWNERSHIP_FIELD,
    )
    if stock_lot:
        frappe.throw(
            f"{document.doctype} {document.name} belongs to CC Stock Lot {stock_lot} "
            "and cannot be deleted"
        )


def _assert_selection_item(frappe: Any, selection: TrackingSelection, item_code: str) -> None:
    item_field = "item" if selection.doctype == "Batch" else "item_code"
    actual_item = frappe.db.get_value(selection.doctype, selection.name, item_field)
    if not actual_item:
        frappe.throw(f"{selection.doctype} {selection.name} was not created by the Stock Entry")
    if actual_item != item_code:
        frappe.throw(
            f"{selection.doctype} {selection.name} belongs to Item {actual_item}, not {item_code}"
        )


def assign_receipt_tracking_ownership(
    frappe: Any,
    *,
    stock_entry_row: Any,
    stock_lot: str,
    tracking_type: str,
) -> dict[str, str | None]:
    """Establish Batch/Serial master ownership after ERPNext creates the inward bundle."""
    selections = get_tracking_selections(frappe, stock_entry_row)
    batch_names = tuple(
        selection.name for selection in selections if selection.doctype == "Batch"
    )
    serial_numbers = tuple(
        selection.name for selection in selections if selection.doctype == "Serial No"
    )

    if tracking_type == TRACKING_NONE:
        if selections:
            frappe.throw(f"Untracked Item {stock_entry_row.item_code} produced Batch/Serial identities")
    elif tracking_type == TRACKING_BATCH:
        if len(batch_names) != 1 or serial_numbers:
            frappe.throw(
                f"Batch receipt row for {stock_entry_row.item_code} must resolve to exactly one Batch"
            )
    elif tracking_type == TRACKING_SERIAL:
        expected_count = int(stock_entry_row.get("transfer_qty") or stock_entry_row.get("qty") or 0)
        if batch_names or len(serial_numbers) != expected_count:
            frappe.throw(
                f"Serial receipt row for {stock_entry_row.item_code} must resolve to "
                f"{expected_count} unique Serial Nos"
            )
    else:
        frappe.throw(f"Unsupported receipt tracking type: {tracking_type}")

    for selection in selections:
        _assert_selection_item(frappe, selection, stock_entry_row.item_code)
        current_owner = frappe.db.get_value(selection.doctype, selection.name, OWNERSHIP_FIELD)
        if current_owner and current_owner != stock_lot:
            frappe.throw(
                f"{selection.doctype} {selection.name} already belongs to CC Stock Lot {current_owner}"
            )
        if not current_owner:
            frappe.db.set_value(
                selection.doctype,
                selection.name,
                OWNERSHIP_FIELD,
                stock_lot,
                update_modified=False,
            )

    return {
        "batch_no": batch_names[0] if batch_names else None,
        "serial_numbers": "\n".join(serial_numbers) or None,
    }
