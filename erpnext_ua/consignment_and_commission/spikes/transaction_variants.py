"""Test-site-only transfer, return and reconciliation Inventory Dimension probes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .inventory_dimension import (
    DIMENSION_FIELD,
    _assert_test_scope,
    _cancel_submitted,
    _dimension_balance,
    _ensure_customer,
    _ensure_dimension,
    _ensure_item,
    _ensure_lot,
    _ensure_reference_doctype,
    _ensure_warehouse,
    _gl_evidence,
    _ledger_evidence,
    _make_sales_invoice,
    _make_stock_entry,
    _root_warehouse,
)

TRANSFER_WAREHOUSE_TITLE = "TP Gate 0B Transfer Warehouse"


def _ensure_transfer_warehouse(frappe: Any, company: str) -> str:
    existing = frappe.db.get_value(
        "Warehouse",
        {"warehouse_name": TRANSFER_WAREHOUSE_TITLE, "company": company},
        "name",
    )
    if existing:
        return existing

    warehouse = frappe.get_doc(
        {
            "doctype": "Warehouse",
            "warehouse_name": TRANSFER_WAREHOUSE_TITLE,
            "company": company,
            "parent_warehouse": _root_warehouse(frappe, company),
            "is_group": 0,
        }
    )
    warehouse.insert(ignore_permissions=True)
    return warehouse.name


def _make_material_transfer(
    *,
    item_code: str,
    company: str,
    source_warehouse: str,
    target_warehouse: str,
    qty: float,
    source_owner: str,
    target_owner: str,
    before_submit: Callable[[Any], None] | None = None,
) -> Any:
    from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

    entry = make_stock_entry(
        item_code=item_code,
        company=company,
        from_warehouse=source_warehouse,
        to_warehouse=target_warehouse,
        qty=qty,
        rate=0,
        do_not_save=True,
    )
    row = entry.items[0]
    row.basic_rate = 0
    row.allow_zero_valuation_rate = 1
    row.set_basic_rate_manually = 1
    row.set(DIMENSION_FIELD, source_owner)
    row.set(f"to_{DIMENSION_FIELD}", target_owner)
    entry.insert(ignore_permissions=True)
    if before_submit:
        before_submit(entry)
    entry.submit()
    return entry


def _make_sales_invoice_return(invoice_name: str) -> Any:
    from erpnext.controllers.sales_and_purchase_return import make_return_doc

    return_invoice = make_return_doc("Sales Invoice", invoice_name)
    return_invoice.insert(ignore_permissions=True)
    return_invoice.submit()
    return return_invoice


def _make_stock_reconciliation(
    frappe: Any,
    *,
    item_code: str,
    company: str,
    warehouse: str,
    owner: str,
    qty: float,
) -> Any:
    from frappe.utils import nowdate

    company_doc = frappe.get_cached_doc("Company", company)
    reconciliation = frappe.new_doc("Stock Reconciliation")
    reconciliation.company = company
    reconciliation.purpose = "Stock Reconciliation"
    reconciliation.posting_date = nowdate()
    reconciliation.expense_account = company_doc.stock_adjustment_account
    reconciliation.cost_center = company_doc.cost_center
    row = reconciliation.append(
        "items",
        {
            "item_code": item_code,
            "warehouse": warehouse,
            "qty": qty,
            "valuation_rate": 0,
            "allow_zero_valuation_rate": 1,
        },
    )
    row.set(DIMENSION_FIELD, owner)
    reconciliation.insert(ignore_permissions=True)
    reconciliation.submit()
    return reconciliation


def _transfer_rows(ledger: list[dict[str, Any]], voucher_no: str) -> list[dict[str, Any]]:
    return [row for row in ledger if row["voucher_no"] == voucher_no]


def run_transaction_variants(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Run transfer, Sales Invoice return and reconciliation dimension checks."""
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
    owner = _ensure_lot(frappe)
    item_code = _ensure_item(frappe)
    source_warehouse = _ensure_warehouse(frappe, company)
    target_warehouse = _ensure_transfer_warehouse(frappe, company)
    customer = _ensure_customer(frappe)
    frappe.db.commit()

    result: dict[str, Any] = {
        "site": frappe.local.site,
        "company": company,
        "dimension": dimension.name,
        "dimension_field": DIMENSION_FIELD,
        "owner": owner,
        "item_code": item_code,
        "source_warehouse": source_warehouse,
        "target_warehouse": target_warehouse,
        "customer": customer,
    }

    flow_documents: list[Any] = []
    try:
        receipt = _make_stock_entry(
            item_code=item_code,
            company=company,
            warehouse=source_warehouse,
            qty=10,
            inward=True,
            dimension_value=owner,
        )
        flow_documents.append(receipt)

        transfer = _make_material_transfer(
            item_code=item_code,
            company=company,
            source_warehouse=source_warehouse,
            target_warehouse=target_warehouse,
            qty=4,
            source_owner=owner,
            target_owner=owner,
        )
        flow_documents.append(transfer)

        invoice = _make_sales_invoice(
            frappe,
            company=company,
            customer=customer,
            item_code=item_code,
            warehouse=target_warehouse,
            lot_id=owner,
            qty=4,
            rate=100,
        )
        flow_documents.append(invoice)

        return_invoice = _make_sales_invoice_return(invoice.name)
        flow_documents.append(return_invoice)

        result["flow_vouchers"] = [document.name for document in flow_documents]
        result["flow_stock_ledger"] = _ledger_evidence(frappe, result["flow_vouchers"])
        result["transfer_stock_ledger"] = _transfer_rows(result["flow_stock_ledger"], transfer.name)
        result["return_stock_ledger"] = _transfer_rows(result["flow_stock_ledger"], return_invoice.name)
        result["return_row_dimension"] = return_invoice.items[0].get(DIMENSION_FIELD)
        result["return_update_stock"] = int(return_invoice.update_stock or 0)
        result["return_gl"] = _gl_evidence(frappe, return_invoice.name)
        result["balances_after_return"] = {
            "source": _dimension_balance(frappe, item_code, source_warehouse, owner),
            "target": _dimension_balance(frappe, item_code, target_warehouse, owner),
        }
    finally:
        result["cancelled_flow_vouchers"] = _cancel_submitted(flow_documents)
        result["balances_after_flow_cleanup"] = {
            "source": _dimension_balance(frappe, item_code, source_warehouse, owner),
            "target": _dimension_balance(frappe, item_code, target_warehouse, owner),
        }

    transfer_ledger = result["transfer_stock_ledger"]
    if len(transfer_ledger) != 2:
        raise AssertionError(f"Expected two Material Transfer SLE rows: {result}")
    expected_transfer = {
        (source_warehouse, -4.0, owner),
        (target_warehouse, 4.0, owner),
    }
    actual_transfer = {
        (row["warehouse"], float(row["actual_qty"] or 0), row[DIMENSION_FIELD]) for row in transfer_ledger
    }
    if actual_transfer != expected_transfer:
        raise AssertionError(f"Material Transfer did not preserve the dimension: {result}")

    return_ledger = result["return_stock_ledger"]
    if len(return_ledger) != 1 or float(return_ledger[0]["actual_qty"] or 0) != 4:
        raise AssertionError(f"Expected one +4 Sales Invoice Return SLE: {result}")
    if return_ledger[0][DIMENSION_FIELD] != owner or result["return_row_dimension"] != owner:
        raise AssertionError(f"Sales Invoice Return did not preserve the dimension: {result}")
    if result["return_update_stock"] != 1:
        raise AssertionError(f"Sales Invoice Return did not retain update_stock: {result}")
    if result["balances_after_return"] != {"source": 6.0, "target": 4.0}:
        raise AssertionError(f"Unexpected balances after transfer/sale/return: {result}")
    if any(result["balances_after_flow_cleanup"].values()):
        raise AssertionError(f"Expected zero balances after flow cleanup: {result}")

    reconciliation_documents: list[Any] = []
    try:
        opening_reconciliation = _make_stock_reconciliation(
            frappe,
            item_code=item_code,
            company=company,
            warehouse=source_warehouse,
            owner=owner,
            qty=5,
        )
        reconciliation_documents.append(opening_reconciliation)
        result["opening_reconciliation"] = opening_reconciliation.name
        result["opening_reconciliation_ledger"] = _ledger_evidence(frappe, [opening_reconciliation.name])
        result["balance_after_opening_reconciliation"] = _dimension_balance(
            frappe, item_code, source_warehouse, owner
        )

        frappe.db.savepoint("gate_0b_existing_dimension_reconciliation")
        try:
            unexpected_reconciliation = _make_stock_reconciliation(
                frappe,
                item_code=item_code,
                company=company,
                warehouse=source_warehouse,
                owner=owner,
                qty=3,
            )
        except frappe.ValidationError as exc:
            frappe.db.rollback(save_point="gate_0b_existing_dimension_reconciliation")
            result["existing_dimension_reconciliation"] = {
                "status": "PASS_REJECTED_BY_ERPNext",
                "exception": type(exc).__name__,
                "message": str(exc),
            }
        else:
            reconciliation_documents.append(unexpected_reconciliation)
            result["existing_dimension_reconciliation"] = {
                "status": "FAIL_ACCEPTED_EXISTING_DIMENSION",
                "exception": None,
                "message": None,
            }
    finally:
        result["cancelled_reconciliation_vouchers"] = _cancel_submitted(reconciliation_documents)
        result["balance_after_reconciliation_cleanup"] = _dimension_balance(
            frappe, item_code, source_warehouse, owner
        )

    opening_ledger = result["opening_reconciliation_ledger"]
    if len(opening_ledger) != 1 or float(opening_ledger[0]["actual_qty"] or 0) != 5:
        raise AssertionError(f"Expected one +5 opening reconciliation SLE: {result}")
    if opening_ledger[0][DIMENSION_FIELD] != owner:
        raise AssertionError(f"Opening reconciliation did not preserve the dimension: {result}")
    if result["balance_after_opening_reconciliation"] != 5:
        raise AssertionError(f"Expected dimension balance 5 after opening reconciliation: {result}")
    if result["existing_dimension_reconciliation"]["status"] != "PASS_REJECTED_BY_ERPNext":
        raise AssertionError(f"Expected ERPNext to reject non-opening dimension reconciliation: {result}")
    if result["balance_after_reconciliation_cleanup"] != 0:
        raise AssertionError(f"Expected zero balance after reconciliation cleanup: {result}")

    return result
