"""Test-site-only ownership checks for ERPNext Serial and Batch Bundle."""

from __future__ import annotations

from typing import Any

from .inventory_dimension import (
    DIMENSION_FIELD,
    REFERENCE_DOCTYPE,
    _assert_test_scope,
    _cancel_submitted,
    _dimension_balance,
    _ensure_dimension,
    _ensure_lot,
    _ensure_lot_value,
    _ensure_reference_doctype,
    _ensure_warehouse,
    _ledger_evidence,
    _make_stock_entry,
)

ALT_LOT_ID = "TP-GATE-0B-LOT-002"
BATCH_ITEM_CODE = "TP-GATE-0B-BATCH-ITEM"
SERIAL_ITEM_CODE = "TP-GATE-0B-SERIAL-ITEM"
BATCH_SERIES = "TP-G0B-B-.#####"
SERIAL_SERIES = "TP-G0B-S-.#####"


def _ensure_tracking_owner_fields(frappe: Any) -> None:
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            doctype: [
                {
                    "fieldname": DIMENSION_FIELD,
                    "label": "TP Spike Lot",
                    "fieldtype": "Link",
                    "options": REFERENCE_DOCTYPE,
                    "read_only": 1,
                    "search_index": 1,
                }
            ]
            for doctype in ["Batch", "Serial No"]
        }
    )


def _set_tracking_owner(frappe: Any, doctype: str, name: str, owner: str) -> None:
    frappe.db.set_value(doctype, name, DIMENSION_FIELD, owner, update_modified=False)


def _validate_draft_tracking_ownership(frappe: Any, document: Any) -> None:
    for row in document.items:
        expected_owner = row.get(DIMENSION_FIELD)
        if not expected_owner:
            continue

        selections: list[tuple[str, str]] = []
        if row.batch_no:
            selections.append(("Batch", row.batch_no))
        if row.serial_no:
            selections.extend(("Serial No", serial_no) for serial_no in row.serial_no.splitlines() if serial_no)
        if row.serial_and_batch_bundle:
            selections.extend(
                ("Serial No", entry["serial_no"]) if entry.get("serial_no") else ("Batch", entry["batch_no"])
                for entry in _bundle_entries(frappe, row.serial_and_batch_bundle)
            )

        if not selections:
            frappe.throw(
                f"Third-party stock row {row.idx} requires an explicit Batch or Serial No",
                frappe.ValidationError,
            )

        for doctype, tracking_name in selections:
            actual_owner = frappe.db.get_value(doctype, tracking_name, DIMENSION_FIELD)
            if actual_owner != expected_owner:
                frappe.throw(
                    f"Tracking ownership mismatch: {doctype} {tracking_name} belongs to "
                    f"{actual_owner or 'no owner'}, not {expected_owner}",
                    frappe.ValidationError,
                )


def _stock_item_defaults(frappe: Any) -> tuple[str, str]:
    item_group = "All Item Groups"
    if not frappe.db.exists("Item Group", item_group):
        item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")

    stock_uom = "Nos"
    if not frappe.db.exists("UOM", stock_uom):
        stock_uom = frappe.db.get_value("UOM", {}, "name")

    if not item_group or not stock_uom:
        raise RuntimeError("A valid Item Group and UOM are required for the Serial/Batch fixture")
    return item_group, stock_uom


def _ensure_tracked_item(frappe: Any, *, item_code: str, batch: bool, serial: bool) -> str:
    if frappe.db.exists("Item", item_code):
        item = frappe.get_cached_doc("Item", item_code)
        if bool(item.has_batch_no) != batch or bool(item.has_serial_no) != serial:
            raise RuntimeError(f"Existing Item {item_code!r} has incompatible serial/batch settings")
        return item_code

    item_group, stock_uom = _stock_item_defaults(frappe)
    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_code,
            "item_group": item_group,
            "stock_uom": stock_uom,
            "is_stock_item": 1,
            "valuation_method": "FIFO",
            "has_batch_no": int(batch),
            "create_new_batch": int(batch),
            "batch_number_series": BATCH_SERIES if batch else None,
            "has_serial_no": int(serial),
            "serial_no_series": SERIAL_SERIES if serial else None,
        }
    ).insert(ignore_permissions=True)
    return item_code


def _bundle_entries(frappe: Any, bundle_name: str) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        "Serial and Batch Entry",
        filters={"parent": bundle_name},
        fields=["serial_no", "batch_no", "qty", "incoming_rate", "stock_value_difference"],
        order_by="idx asc",
    )
    return [dict(row) for row in rows]


def _bundle_evidence(frappe: Any, bundle_names: list[str]) -> list[dict[str, Any]]:
    evidence = []
    for bundle_name in bundle_names:
        bundle = frappe.get_doc("Serial and Batch Bundle", bundle_name)
        evidence.append(
            {
                "name": bundle.name,
                "voucher_type": bundle.voucher_type,
                "voucher_no": bundle.voucher_no,
                "voucher_detail_no": bundle.voucher_detail_no,
                "item_code": bundle.item_code,
                "warehouse": bundle.warehouse,
                "type_of_transaction": bundle.type_of_transaction,
                "total_qty": float(bundle.total_qty or 0),
                "entries": _bundle_entries(frappe, bundle.name),
            }
        )
    return evidence


def _reload_bundle(document: Any) -> str:
    document.reload()
    bundle_name = document.items[0].serial_and_batch_bundle
    if not bundle_name:
        raise AssertionError(f"No Serial and Batch Bundle created for {document.name}")
    return bundle_name


def _first_tracking_value(entries: list[dict[str, Any]], fieldname: str) -> str:
    values = [row[fieldname] for row in entries if row.get(fieldname)]
    if len(values) != 1:
        raise AssertionError(f"Expected one {fieldname} in bundle entries: {entries}")
    return values[0]


def _try_cross_owner_issue(
    frappe: Any,
    *,
    savepoint: str,
    item_code: str,
    company: str,
    warehouse: str,
    batch_no: str | None = None,
    serial_no: str | None = None,
) -> tuple[Any | None, dict[str, Any]]:
    frappe.db.savepoint(savepoint)
    try:
        issue = _make_stock_entry(
            item_code=item_code,
            company=company,
            warehouse=warehouse,
            qty=1,
            inward=False,
            dimension_value=ALT_LOT_ID,
            batch_no=batch_no,
            serial_no=serial_no,
            use_serial_batch_fields=True,
        )
    except frappe.ValidationError as exc:
        frappe.db.rollback(save_point=savepoint)
        return None, {
            "status": "REJECTED",
            "exception": type(exc).__name__,
            "message": str(exc),
        }

    return issue, {
        "status": "FAIL_ACCEPTED_CROSS_OWNER",
        "exception": None,
        "message": None,
    }


def _try_application_guard(
    frappe: Any,
    *,
    savepoint: str,
    item_code: str,
    company: str,
    warehouse: str,
    batch_no: str | None = None,
    serial_no: str | None = None,
) -> dict[str, Any]:
    frappe.db.savepoint(savepoint)
    try:
        issue = _make_stock_entry(
            item_code=item_code,
            company=company,
            warehouse=warehouse,
            qty=1,
            inward=False,
            dimension_value=ALT_LOT_ID,
            batch_no=batch_no,
            serial_no=serial_no,
            use_serial_batch_fields=True,
            before_submit=lambda document: _validate_draft_tracking_ownership(frappe, document),
        )
    except frappe.ValidationError as exc:
        frappe.db.rollback(save_point=savepoint)
        return {
            "status": "PASS_REJECTED_CROSS_OWNER",
            "exception": type(exc).__name__,
            "message": str(exc),
        }

    issue.cancel()
    return {
        "status": "FAIL_ACCEPTED_CROSS_OWNER",
        "exception": None,
        "message": None,
        "unexpected_voucher": issue.name,
    }


def run_serial_batch_flow(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Probe bundle propagation and native cross-owner consistency checks."""
    import frappe
    from erpnext.stock.doctype.batch.batch import get_batch_qty

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")

    _ensure_reference_doctype(frappe)
    dimension = _ensure_dimension(frappe)
    owner_a = _ensure_lot(frappe)
    owner_b = _ensure_lot_value(frappe, ALT_LOT_ID)
    warehouse = _ensure_warehouse(frappe, company)
    batch_item = _ensure_tracked_item(frappe, item_code=BATCH_ITEM_CODE, batch=True, serial=False)
    serial_item = _ensure_tracked_item(frappe, item_code=SERIAL_ITEM_CODE, batch=False, serial=True)

    direct_dimension_fields_before_fallback = {
        doctype: bool(frappe.get_meta(doctype).has_field(DIMENSION_FIELD))
        for doctype in ["Serial and Batch Bundle", "Serial and Batch Entry", "Batch", "Serial No"]
    }
    _ensure_tracking_owner_fields(frappe)
    direct_dimension_fields_after_fallback = {
        doctype: bool(frappe.get_meta(doctype).has_field(DIMENSION_FIELD))
        for doctype in ["Serial and Batch Bundle", "Serial and Batch Entry", "Batch", "Serial No"]
    }
    original_bundle_setting = int(
        frappe.db.get_single_value("Stock Settings", "enable_serial_and_batch_no_for_item") or 0
    )
    frappe.db.commit()
    frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)
    frappe.clear_cache(doctype="Stock Settings")

    submitted: list[Any] = []
    result: dict[str, Any] = {
        "site": frappe.local.site,
        "company": company,
        "dimension": dimension.name,
        "dimension_field": DIMENSION_FIELD,
        "owners": [owner_a, owner_b],
        "warehouse": warehouse,
        "batch_item": batch_item,
        "serial_item": serial_item,
        "direct_dimension_fields_before_fallback": direct_dimension_fields_before_fallback,
        "direct_dimension_fields_after_fallback": direct_dimension_fields_after_fallback,
        "original_bundle_setting": original_bundle_setting,
    }

    batch_a = None
    batch_b = None
    serial_a = None
    serial_b = None
    try:
        batch_receipt_a = _make_stock_entry(
            item_code=batch_item,
            company=company,
            warehouse=warehouse,
            qty=2,
            inward=True,
            dimension_value=owner_a,
            use_serial_batch_fields=True,
        )
        submitted.append(batch_receipt_a)
        batch_bundle_a = _reload_bundle(batch_receipt_a)
        batch_a = _first_tracking_value(_bundle_entries(frappe, batch_bundle_a), "batch_no")
        _set_tracking_owner(frappe, "Batch", batch_a, owner_a)

        batch_receipt_b = _make_stock_entry(
            item_code=batch_item,
            company=company,
            warehouse=warehouse,
            qty=2,
            inward=True,
            dimension_value=owner_b,
            use_serial_batch_fields=True,
        )
        submitted.append(batch_receipt_b)
        batch_bundle_b = _reload_bundle(batch_receipt_b)
        batch_b = _first_tracking_value(_bundle_entries(frappe, batch_bundle_b), "batch_no")
        _set_tracking_owner(frappe, "Batch", batch_b, owner_b)

        result["batch_application_owner_guard"] = _try_application_guard(
            frappe,
            savepoint="gate_0b_batch_application_guard",
            item_code=batch_item,
            company=company,
            warehouse=warehouse,
            batch_no=batch_a,
        )

        batch_issue, batch_guard = _try_cross_owner_issue(
            frappe,
            savepoint="gate_0b_batch_owner_mismatch",
            item_code=batch_item,
            company=company,
            warehouse=warehouse,
            batch_no=batch_a,
        )
        result["batch_native_owner_guard"] = batch_guard
        if batch_issue:
            submitted.append(batch_issue)
            batch_issue_bundle = _reload_bundle(batch_issue)
        else:
            batch_issue_bundle = None

        serial_receipt_a = _make_stock_entry(
            item_code=serial_item,
            company=company,
            warehouse=warehouse,
            qty=1,
            inward=True,
            dimension_value=owner_a,
            use_serial_batch_fields=True,
        )
        submitted.append(serial_receipt_a)
        serial_bundle_a = _reload_bundle(serial_receipt_a)
        serial_a = _first_tracking_value(_bundle_entries(frappe, serial_bundle_a), "serial_no")
        _set_tracking_owner(frappe, "Serial No", serial_a, owner_a)

        serial_receipt_b = _make_stock_entry(
            item_code=serial_item,
            company=company,
            warehouse=warehouse,
            qty=1,
            inward=True,
            dimension_value=owner_b,
            use_serial_batch_fields=True,
        )
        submitted.append(serial_receipt_b)
        serial_bundle_b = _reload_bundle(serial_receipt_b)
        serial_b = _first_tracking_value(_bundle_entries(frappe, serial_bundle_b), "serial_no")
        _set_tracking_owner(frappe, "Serial No", serial_b, owner_b)

        result["serial_application_owner_guard"] = _try_application_guard(
            frappe,
            savepoint="gate_0b_serial_application_guard",
            item_code=serial_item,
            company=company,
            warehouse=warehouse,
            serial_no=serial_a,
        )

        serial_issue, serial_guard = _try_cross_owner_issue(
            frappe,
            savepoint="gate_0b_serial_owner_mismatch",
            item_code=serial_item,
            company=company,
            warehouse=warehouse,
            serial_no=serial_a,
        )
        result["serial_native_owner_guard"] = serial_guard
        if serial_issue:
            submitted.append(serial_issue)
            serial_issue_bundle = _reload_bundle(serial_issue)
        else:
            serial_issue_bundle = None

        bundle_names = [batch_bundle_a, batch_bundle_b, serial_bundle_a, serial_bundle_b]
        bundle_names.extend(name for name in [batch_issue_bundle, serial_issue_bundle] if name)
        result["tracking_values"] = {
            "batch_owner_a": batch_a,
            "batch_owner_b": batch_b,
            "serial_owner_a": serial_a,
            "serial_owner_b": serial_b,
        }
        result["submitted_vouchers"] = [document.name for document in submitted]
        result["stock_ledger"] = _ledger_evidence(frappe, result["submitted_vouchers"])
        result["bundles"] = _bundle_evidence(frappe, bundle_names)
        result["balances_before_cleanup"] = {
            "batch_owner_a": _dimension_balance(frappe, batch_item, warehouse, owner_a),
            "batch_owner_b": _dimension_balance(frappe, batch_item, warehouse, owner_b),
            "serial_owner_a": _dimension_balance(frappe, serial_item, warehouse, owner_a),
            "serial_owner_b": _dimension_balance(frappe, serial_item, warehouse, owner_b),
        }
    finally:
        try:
            result["cancelled_vouchers"] = _cancel_submitted(submitted)
            result["balances_after_cleanup"] = {
                "batch_owner_a": _dimension_balance(frappe, batch_item, warehouse, owner_a),
                "batch_owner_b": _dimension_balance(frappe, batch_item, warehouse, owner_b),
                "serial_owner_a": _dimension_balance(frappe, serial_item, warehouse, owner_a),
                "serial_owner_b": _dimension_balance(frappe, serial_item, warehouse, owner_b),
            }
            if batch_a:
                result["batch_owner_a_qty_after_cleanup"] = float(
                    get_batch_qty(batch_a, warehouse, batch_item) or 0
                )
            if serial_a:
                result["serial_owner_a_warehouse_after_cleanup"] = frappe.db.get_value(
                    "Serial No", serial_a, "warehouse"
                )
        finally:
            frappe.db.set_single_value(
                "Stock Settings",
                "enable_serial_and_batch_no_for_item",
                original_bundle_setting,
            )
            frappe.clear_cache(doctype="Stock Settings")
            result["restored_bundle_setting"] = int(
                frappe.db.get_single_value("Stock Settings", "enable_serial_and_batch_no_for_item") or 0
            )

    if any(result["balances_after_cleanup"].values()):
        raise AssertionError(f"Expected zero dimension balances after Serial/Batch cleanup: {result}")
    if result.get("batch_owner_a_qty_after_cleanup") != 0:
        raise AssertionError(f"Expected zero batch qty after cleanup: {result}")
    if result.get("serial_owner_a_warehouse_after_cleanup"):
        raise AssertionError(f"Expected serial number to leave the warehouse after cleanup: {result}")
    if result["restored_bundle_setting"] != original_bundle_setting:
        raise AssertionError(f"Expected the original Stock Settings value after cleanup: {result}")
    for guard_name in ["batch_application_owner_guard", "serial_application_owner_guard"]:
        if result[guard_name]["status"] != "PASS_REJECTED_CROSS_OWNER":
            raise AssertionError(f"Expected application ownership guard to reject mismatch: {result}")

    return result
