"""Test-site-only Inventory Dimension material-flow spike.

This module is deliberately not imported by hooks and exposes no whitelisted API.
It must be invoked explicitly with ``bench execute`` on the allow-listed test site.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

ALLOWED_SITES = frozenset({"postest.local"})
ALLOWED_COMPANIES = frozenset({"POS Test Ukraine"})
CONFIRMATION = "RUN_GATE_0B"

REFERENCE_DOCTYPE = "TP Spike Lot"
DIMENSION_NAME = "TP Spike Lot"
DIMENSION_FIELD = "tp_spike_lot"
LOT_ID = "TP-GATE-0B-LOT-001"
ITEM_CODE = "TP-GATE-0B-ZERO-VALUE-ITEM"
WAREHOUSE_TITLE = "TP Gate 0B Warehouse"
CUSTOMER_NAME = "TP Gate 0B Customer"


def _assert_test_scope(frappe: Any, *, confirm_site: str, confirm_write: str, company: str) -> None:
    actual_site = frappe.local.site
    if actual_site not in ALLOWED_SITES or confirm_site != actual_site:
        raise RuntimeError(
            f"Gate 0B writes are allowed only on {sorted(ALLOWED_SITES)}; "
            f"actual={actual_site!r}, confirmed={confirm_site!r}"
        )

    if confirm_write != CONFIRMATION:
        raise RuntimeError(f"Explicit confirmation {CONFIRMATION!r} is required")

    if company not in ALLOWED_COMPANIES:
        raise RuntimeError(f"Gate 0B company must be one of {sorted(ALLOWED_COMPANIES)}")


def _ensure_reference_doctype(frappe: Any) -> None:
    if frappe.db.exists("DocType", REFERENCE_DOCTYPE):
        return

    frappe.get_doc(
        {
            "doctype": "DocType",
            "name": REFERENCE_DOCTYPE,
            "module": "Consignment and Commission",
            "custom": 1,
            "autoname": "field:lot_id",
            "naming_rule": "By fieldname",
            "fields": [
                {
                    "fieldname": "lot_id",
                    "label": "Lot ID",
                    "fieldtype": "Data",
                    "reqd": 1,
                    "unique": 1,
                    "in_list_view": 1,
                }
            ],
            "permissions": [
                {
                    "role": "System Manager",
                    "read": 1,
                    "write": 1,
                    "create": 1,
                    "delete": 1,
                }
            ],
        }
    ).insert(ignore_permissions=True)


def _ensure_dimension(frappe: Any) -> Any:
    if frappe.db.exists("Inventory Dimension", DIMENSION_NAME):
        dimension = frappe.get_doc("Inventory Dimension", DIMENSION_NAME)
        if dimension.reference_document != REFERENCE_DOCTYPE:
            raise RuntimeError(
                f"Existing Inventory Dimension {DIMENSION_NAME!r} uses "
                f"{dimension.reference_document!r}, expected {REFERENCE_DOCTYPE!r}"
            )
        if not dimension.apply_to_all_doctypes or not dimension.validate_negative_stock:
            raise RuntimeError(
                f"Existing Inventory Dimension {DIMENSION_NAME!r} does not have the spike safety settings"
            )
        return dimension

    dimension = frappe.get_doc(
        {
            "doctype": "Inventory Dimension",
            "dimension_name": DIMENSION_NAME,
            "reference_document": REFERENCE_DOCTYPE,
            "apply_to_all_doctypes": 1,
            "validate_negative_stock": 1,
        }
    )
    dimension.insert(ignore_permissions=True)
    return dimension


def _ensure_lot(frappe: Any) -> str:
    return _ensure_lot_value(frappe, LOT_ID)


def _ensure_lot_value(frappe: Any, lot_id: str) -> str:
    if not frappe.db.exists(REFERENCE_DOCTYPE, lot_id):
        frappe.get_doc({"doctype": REFERENCE_DOCTYPE, "lot_id": lot_id}).insert(ignore_permissions=True)
    return lot_id


def _ensure_item(frappe: Any) -> str:
    if frappe.db.exists("Item", ITEM_CODE):
        return ITEM_CODE

    item_group = "All Item Groups"
    if not frappe.db.exists("Item Group", item_group):
        item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")

    stock_uom = "Nos"
    if not frappe.db.exists("UOM", stock_uom):
        stock_uom = frappe.db.get_value("UOM", {}, "name")

    if not item_group or not stock_uom:
        raise RuntimeError("A valid Item Group and UOM are required for the Gate 0B fixture")

    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": ITEM_CODE,
            "item_name": "TP Gate 0B zero-value item",
            "item_group": item_group,
            "stock_uom": stock_uom,
            "is_stock_item": 1,
            "valuation_method": "FIFO",
        }
    ).insert(ignore_permissions=True)
    return ITEM_CODE


def _root_warehouse(frappe: Any, company: str) -> str:
    warehouses = frappe.get_all(
        "Warehouse",
        filters={"company": company, "is_group": 1},
        fields=["name", "parent_warehouse", "lft"],
        order_by="lft asc",
    )
    for warehouse in warehouses:
        if not warehouse.parent_warehouse:
            return warehouse.name
    raise RuntimeError(f"No root group warehouse found for {company!r}")


def _ensure_warehouse(frappe: Any, company: str) -> str:
    existing = frappe.db.get_value(
        "Warehouse",
        {"warehouse_name": WAREHOUSE_TITLE, "company": company},
        "name",
    )
    if existing:
        return existing

    warehouse = frappe.get_doc(
        {
            "doctype": "Warehouse",
            "warehouse_name": WAREHOUSE_TITLE,
            "company": company,
            "parent_warehouse": _root_warehouse(frappe, company),
            "is_group": 0,
        }
    )
    warehouse.insert(ignore_permissions=True)
    return warehouse.name


def _ensure_customer(frappe: Any) -> str:
    existing = frappe.db.get_value("Customer", {"customer_name": CUSTOMER_NAME}, "name")
    if existing:
        return existing

    customer_group = frappe.db.get_single_value("Selling Settings", "customer_group") or "All Customer Groups"
    territory = frappe.db.get_single_value("Selling Settings", "territory") or "All Territories"
    if frappe.db.get_value("Customer Group", customer_group, "is_group"):
        customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    if frappe.db.get_value("Territory", territory, "is_group"):
        territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if not customer_group or not territory:
        raise RuntimeError("A leaf Customer Group and Territory are required for the Gate 0B fixture")

    customer = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": CUSTOMER_NAME,
            "customer_type": "Individual",
            "customer_group": customer_group,
            "territory": territory,
        }
    )
    customer.insert(ignore_permissions=True)
    return customer.name


def _make_stock_entry(
    *,
    item_code: str,
    company: str,
    warehouse: str,
    qty: float,
    inward: bool,
    dimension_value: str | None,
    batch_no: str | None = None,
    serial_no: str | None = None,
    use_serial_batch_fields: bool = False,
    before_submit: Callable[[Any], None] | None = None,
    rate: float = 0,
    posting_date: str | None = None,
    posting_time: str | None = None,
) -> Any:
    from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

    entry = make_stock_entry(
        item_code=item_code,
        company=company,
        to_warehouse=warehouse if inward else None,
        from_warehouse=None if inward else warehouse,
        qty=qty,
        rate=rate,
        posting_date=posting_date,
        posting_time=posting_time,
        batch_no=batch_no,
        serial_no=serial_no,
        use_serial_batch_fields=int(use_serial_batch_fields),
        do_not_save=True,
    )
    row = entry.items[0]
    row.basic_rate = rate
    row.allow_zero_valuation_rate = int(rate == 0)
    row.set_basic_rate_manually = 1
    if dimension_value:
        row.set(f"to_{DIMENSION_FIELD}" if inward else DIMENSION_FIELD, dimension_value)

    entry.insert(ignore_permissions=True)
    if before_submit:
        before_submit(entry)
    entry.submit()
    return entry


def _erpnext_reported_balance(item_code: str, warehouse: str, lot_id: str) -> float:
    from erpnext.stock.utils import get_stock_balance

    return float(
        get_stock_balance(
            item_code,
            warehouse,
            inventory_dimensions_dict={DIMENSION_FIELD: lot_id},
        )
        or 0
    )


def _dimension_balance(frappe: Any, item_code: str, warehouse: str, lot_id: str) -> float:
    """Calculate the active balance for one dimension value from its SLE rows.

    ERPNext's ``get_stock_balance`` filters which prior SLE is selected, but
    returns that row's warehouse-wide ``qty_after_transaction``. That is not a
    dimension-level balance when dimensioned and unassigned rows coexist.
    """
    rows = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "item_code": item_code,
            "warehouse": warehouse,
            "is_cancelled": 0,
            DIMENSION_FIELD: lot_id,
        },
        fields=["actual_qty"],
    )
    return float(sum(float(row.actual_qty or 0) for row in rows))


def _ledger_evidence(frappe: Any, voucher_names: list[str]) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        "Stock Ledger Entry",
        filters={"voucher_no": ("in", voucher_names)},
        fields=[
            "voucher_type",
            "voucher_no",
            "voucher_detail_no",
            "warehouse",
            "item_code",
            "actual_qty",
            "valuation_rate",
            "stock_value_difference",
            "serial_and_batch_bundle",
            DIMENSION_FIELD,
        ],
        order_by="creation asc",
    )
    return [dict(row) for row in rows]


def _make_sales_invoice(
    frappe: Any,
    *,
    company: str,
    customer: str,
    item_code: str,
    warehouse: str,
    lot_id: str,
    qty: float,
    rate: float,
) -> Any:
    from frappe.utils import nowdate

    company_doc = frappe.get_cached_doc("Company", company)
    invoice = frappe.new_doc("Sales Invoice")
    invoice.company = company
    invoice.customer = customer
    invoice.posting_date = nowdate()
    invoice.due_date = nowdate()
    invoice.update_stock = 1
    invoice.currency = company_doc.default_currency
    invoice.conversion_rate = 1
    invoice.debit_to = company_doc.default_receivable_account
    row = invoice.append(
        "items",
        {
            "item_code": item_code,
            "item_name": "TP Gate 0B zero-value item",
            "description": "Gate 0B Sales Invoice ownership propagation",
            "warehouse": warehouse,
            "qty": qty,
            "uom": "Nos",
            "stock_uom": "Nos",
            "conversion_factor": 1,
            "rate": rate,
            "price_list_rate": rate,
            "income_account": company_doc.default_income_account,
            "expense_account": company_doc.default_expense_account,
            "cost_center": company_doc.cost_center,
            "allow_zero_valuation_rate": 1,
        },
    )
    row.set(DIMENSION_FIELD, lot_id)
    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _gl_evidence(frappe: Any, invoice_name: str) -> list[dict[str, Any]]:
    rows = frappe.get_all(
        "GL Entry",
        filters={"voucher_type": "Sales Invoice", "voucher_no": invoice_name, "is_cancelled": 0},
        fields=[
            "account",
            "debit",
            "credit",
            "account_currency",
            "debit_in_account_currency",
            "credit_in_account_currency",
        ],
        order_by="creation asc",
    )
    return [dict(row) for row in rows]


def _cancel_submitted(documents: list[Any]) -> list[str]:
    cancelled = []
    for document in reversed(documents):
        document.reload()
        if document.docstatus == 1:
            document.cancel()
            cancelled.append(document.name)
    return cancelled


def run_material_flow(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Run Gate 0B/0C material-flow checks and return serializable evidence.

    Persistent setup is limited to a custom reference DocType, one Inventory
    Dimension, one lot, one Item and one Warehouse on the allow-listed test site.
    Submitted Stock Entries are cancelled before the runner returns.
    """
    import frappe
    from erpnext.stock.doctype.stock_ledger_entry.stock_ledger_entry import (
        InventoryDimensionNegativeStockError,
    )

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")

    _ensure_reference_doctype(frappe)
    dimension = _ensure_dimension(frappe)
    lot_id = _ensure_lot(frappe)
    item_code = _ensure_item(frappe)
    warehouse = _ensure_warehouse(frappe, company)

    expected_fields = {
        "Stock Entry Detail": [DIMENSION_FIELD, f"to_{DIMENSION_FIELD}"],
        "Stock Ledger Entry": [DIMENSION_FIELD],
    }
    missing_fields = [
        f"{doctype}.{fieldname}"
        for doctype, fieldnames in expected_fields.items()
        for fieldname in fieldnames
        if not frappe.get_meta(doctype).has_field(fieldname)
    ]
    if missing_fields:
        raise RuntimeError(f"Inventory Dimension did not create fields: {missing_fields}")

    # Inventory Dimension creation adds schema and custom fields. Persist setup
    # before transactional savepoints so a failed negative-stock probe can be
    # rolled back independently.
    frappe.db.commit()

    submitted: list[Any] = []
    result: dict[str, Any] = {
        "site": frappe.local.site,
        "company": company,
        "dimension": dimension.name,
        "dimension_field": DIMENSION_FIELD,
        "reference_doctype": REFERENCE_DOCTYPE,
        "lot_id": lot_id,
        "item_code": item_code,
        "warehouse": warehouse,
        "missing_fields": missing_fields,
    }

    try:
        assigned_receipt = _make_stock_entry(
            item_code=item_code,
            company=company,
            warehouse=warehouse,
            qty=10,
            inward=True,
            dimension_value=lot_id,
        )
        submitted.append(assigned_receipt)

        unassigned_receipt = _make_stock_entry(
            item_code=item_code,
            company=company,
            warehouse=warehouse,
            qty=100,
            inward=True,
            dimension_value=None,
        )
        submitted.append(unassigned_receipt)

        successful_issue = _make_stock_entry(
            item_code=item_code,
            company=company,
            warehouse=warehouse,
            qty=4,
            inward=False,
            dimension_value=lot_id,
        )
        submitted.append(successful_issue)

        result["balance_after_successful_issue"] = _dimension_balance(frappe, item_code, warehouse, lot_id)
        result["erpnext_reported_balance_after_successful_issue"] = _erpnext_reported_balance(
            item_code, warehouse, lot_id
        )
        result["submitted_vouchers"] = [document.name for document in submitted]
        result["ledger_before_negative_probe"] = _ledger_evidence(frappe, result["submitted_vouchers"])

        frappe.db.savepoint("gate_0b_negative_dimension")
        try:
            # Total warehouse balance is 106, but the selected lot has only 6.
            # The dimension-level validator must therefore reject a qty of 7.
            unexpected_issue = _make_stock_entry(
                item_code=item_code,
                company=company,
                warehouse=warehouse,
                qty=7,
                inward=False,
                dimension_value=lot_id,
            )
        except InventoryDimensionNegativeStockError as exc:
            frappe.db.rollback(save_point="gate_0b_negative_dimension")
            result["negative_dimension_validation"] = "PASS"
            result["negative_dimension_error"] = str(exc)
        else:
            submitted.append(unexpected_issue)
            result["negative_dimension_validation"] = "FAIL"
            result["negative_dimension_error"] = None

        result["balance_after_negative_probe"] = _dimension_balance(frappe, item_code, warehouse, lot_id)
        result["zero_valuation"] = all(
            float(row["valuation_rate"] or 0) == 0 and float(row["stock_value_difference"] or 0) == 0
            for row in result["ledger_before_negative_probe"]
        )
    finally:
        result["cancelled_vouchers"] = _cancel_submitted(submitted)
        result["balance_after_cleanup"] = _dimension_balance(frappe, item_code, warehouse, lot_id)

    if result.get("negative_dimension_validation") != "PASS":
        raise AssertionError(f"Dimension-level negative stock validation failed: {result}")
    if result["balance_after_successful_issue"] != 6:
        raise AssertionError(f"Expected dimension balance 6 after issue: {result}")
    if not result["zero_valuation"]:
        raise AssertionError(f"Expected zero valuation Stock Ledger Entries: {result}")
    if result["balance_after_cleanup"] != 0:
        raise AssertionError(f"Expected zero dimension balance after cleanup: {result}")

    return result


def run_sales_invoice_flow(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Verify Sales Invoice ``update_stock`` dimension propagation and zero COGS."""
    import frappe

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")

    _ensure_reference_doctype(frappe)
    dimension = _ensure_dimension(frappe)
    lot_id = _ensure_lot(frappe)
    item_code = _ensure_item(frappe)
    warehouse = _ensure_warehouse(frappe, company)
    customer = _ensure_customer(frappe)

    missing_fields = [
        f"Sales Invoice Item.{fieldname}"
        for fieldname in [DIMENSION_FIELD, f"to_{DIMENSION_FIELD}"]
        if not frappe.get_meta("Sales Invoice Item").has_field(fieldname)
    ]
    if missing_fields:
        raise RuntimeError(f"Inventory Dimension did not create fields: {missing_fields}")

    frappe.db.commit()

    submitted: list[Any] = []
    result: dict[str, Any] = {
        "site": frappe.local.site,
        "company": company,
        "dimension": dimension.name,
        "dimension_field": DIMENSION_FIELD,
        "lot_id": lot_id,
        "item_code": item_code,
        "warehouse": warehouse,
        "customer": customer,
        "missing_fields": missing_fields,
    }

    try:
        receipt = _make_stock_entry(
            item_code=item_code,
            company=company,
            warehouse=warehouse,
            qty=10,
            inward=True,
            dimension_value=lot_id,
        )
        submitted.append(receipt)

        invoice = _make_sales_invoice(
            frappe,
            company=company,
            customer=customer,
            item_code=item_code,
            warehouse=warehouse,
            lot_id=lot_id,
            qty=4,
            rate=100,
        )
        submitted.append(invoice)

        result["submitted_vouchers"] = [document.name for document in submitted]
        result["invoice_stock_ledger"] = _ledger_evidence(frappe, [invoice.name])
        result["invoice_gl"] = _gl_evidence(frappe, invoice.name)
        result["balance_after_invoice"] = _dimension_balance(frappe, item_code, warehouse, lot_id)

        company_doc = frappe.get_cached_doc("Company", company)
        stock_accounts = {company_doc.default_expense_account, company_doc.default_inventory_account}
        result["nonzero_stock_gl"] = [
            row
            for row in result["invoice_gl"]
            if row["account"] in stock_accounts
            and (abs(float(row["debit"] or 0)) > 0.000001 or abs(float(row["credit"] or 0)) > 0.000001)
        ]
    finally:
        result["cancelled_vouchers"] = _cancel_submitted(submitted)
        result["balance_after_cleanup"] = _dimension_balance(frappe, item_code, warehouse, lot_id)

    ledger = result["invoice_stock_ledger"]
    if len(ledger) != 1:
        raise AssertionError(f"Expected one Sales Invoice SLE: {result}")
    if float(ledger[0]["actual_qty"] or 0) != -4 or ledger[0][DIMENSION_FIELD] != lot_id:
        raise AssertionError(f"Sales Invoice did not propagate its dimension: {result}")
    if float(ledger[0]["valuation_rate"] or 0) != 0 or float(ledger[0]["stock_value_difference"] or 0) != 0:
        raise AssertionError(f"Expected zero-valued Sales Invoice SLE: {result}")
    if result["balance_after_invoice"] != 6:
        raise AssertionError(f"Expected dimension balance 6 after Sales Invoice: {result}")
    if result["nonzero_stock_gl"]:
        raise AssertionError(f"Expected no stock/COGS value for zero-valued stock: {result}")
    if result["balance_after_cleanup"] != 0:
        raise AssertionError(f"Expected zero dimension balance after cleanup: {result}")

    return result
