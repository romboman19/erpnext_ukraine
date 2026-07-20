"""Test-site-only global FIFO, valuation and last-unit reservation probes."""

from __future__ import annotations

from decimal import Decimal
from time import sleep
from typing import Any

from ..services.allocation import AllocationSlice, StockCandidate, allocate_global_fifo
from .inventory_dimension import (
    DIMENSION_FIELD,
    ITEM_CODE,
    _assert_test_scope,
    _cancel_submitted,
    _dimension_balance,
    _ensure_customer,
    _ensure_dimension,
    _ensure_item,
    _ensure_lot_value,
    _ensure_reference_doctype,
    _gl_evidence,
    _ledger_evidence,
    _make_stock_entry,
    _root_warehouse,
)

LOCATION_TITLE = "TP Gate 0C Location"
WAREHOUSE_FIELD = "tp_spike_stock_type"
WAREHOUSE_TITLES = {
    "OWN": "TP Gate 0C Own",
    "COMMISSION": "TP Gate 0C Commission",
    "CONSIGNMENT": "TP Gate 0C Consignment",
}
LOT_NAMES = {
    "OWN": "TP-GATE-0C-OWN-LOT",
    "COMMISSION": "TP-GATE-0C-COMMISSION-LOT",
    "CONSIGNMENT": "TP-GATE-0C-CONSIGNMENT-LOT",
}
RECEIPT_TIMES = {
    "COMMISSION": "08:00:00",
    "OWN": "09:00:00",
    "CONSIGNMENT": "10:00:00",
}
RECEIPT_RATES = {"OWN": 50.0, "COMMISSION": 0.0, "CONSIGNMENT": 0.0}

RESERVATION_DOCTYPE = "TP Spike Reservation Lot"
RESERVATION_LOT = "TP-GATE-0C-LAST-UNIT"


def _ensure_warehouse_type_field(frappe: Any) -> None:
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            "Warehouse": [
                {
                    "fieldname": WAREHOUSE_FIELD,
                    "label": "TP Spike Stock Type",
                    "fieldtype": "Select",
                    "options": "\nOWN\nCOMMISSION\nCONSIGNMENT",
                    "read_only": 1,
                    "search_index": 1,
                }
            ]
        }
    )


def _ensure_location_warehouses(frappe: Any, company: str) -> tuple[str, dict[str, str]]:
    _ensure_warehouse_type_field(frappe)
    group_name = frappe.db.get_value(
        "Warehouse",
        {"warehouse_name": LOCATION_TITLE, "company": company},
        "name",
    )
    if not group_name:
        group = frappe.get_doc(
            {
                "doctype": "Warehouse",
                "warehouse_name": LOCATION_TITLE,
                "company": company,
                "parent_warehouse": _root_warehouse(frappe, company),
                "is_group": 1,
            }
        )
        group.insert(ignore_permissions=True)
        group_name = group.name

    warehouses = {}
    for stock_type, warehouse_title in WAREHOUSE_TITLES.items():
        warehouse_name = frappe.db.get_value(
            "Warehouse",
            {"warehouse_name": warehouse_title, "company": company},
            "name",
        )
        if not warehouse_name:
            warehouse = frappe.get_doc(
                {
                    "doctype": "Warehouse",
                    "warehouse_name": warehouse_title,
                    "company": company,
                    "parent_warehouse": group_name,
                    "is_group": 0,
                }
            )
            warehouse.insert(ignore_permissions=True)
            warehouse_name = warehouse.name
        frappe.db.set_value("Warehouse", warehouse_name, WAREHOUSE_FIELD, stock_type, update_modified=False)
        warehouses[stock_type] = warehouse_name
    return group_name, warehouses


def _candidate_from_ledger(
    frappe: Any,
    *,
    location: str,
    model: str,
    lot_name: str,
    warehouse: str,
) -> StockCandidate:
    rows = frappe.get_all(
        "Stock Ledger Entry",
        filters={
            "item_code": ITEM_CODE,
            "warehouse": warehouse,
            "is_cancelled": 0,
            DIMENSION_FIELD: lot_name,
        },
        fields=[
            "actual_qty",
            "posting_datetime",
            "voucher_no",
            "voucher_detail_no",
            "creation",
        ],
        order_by="posting_datetime asc, creation asc",
    )
    available_qty = Decimal(str(sum(float(row.actual_qty or 0) for row in rows)))
    inward = next((row for row in rows if float(row.actual_qty or 0) > 0), None)
    if not inward:
        raise AssertionError(f"No inward SLE found for FIFO lot {lot_name}")

    from frappe.utils import get_datetime

    row_index = int(frappe.db.get_value("Stock Entry Detail", inward.voucher_detail_no, "idx") or 0)
    return StockCandidate(
        lot_name=lot_name,
        item_code=ITEM_CODE,
        warehouse=warehouse,
        location=location,
        source_method={
            "OWN": "BUYOUT",
            "COMMISSION": "COMMISSION",
            "CONSIGNMENT": "CONSIGNMENT",
        }[model],
        relationship_model=model,
        fifo_datetime=get_datetime(inward.posting_datetime),
        receipt_name=inward.voucher_no,
        receipt_row_index=row_index,
        available_qty=available_qty,
    )


def _load_candidates(
    frappe: Any,
    *,
    location: str,
    warehouses: dict[str, str],
) -> list[StockCandidate]:
    return [
        _candidate_from_ledger(
            frappe,
            location=location,
            model=model,
            lot_name=LOT_NAMES[model],
            warehouse=warehouses[model],
        )
        for model in ["OWN", "COMMISSION", "CONSIGNMENT"]
    ]


def _allocation_evidence(allocations: list[AllocationSlice]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": row.sequence,
            "lot_name": row.lot_name,
            "warehouse": row.warehouse,
            "source_method": row.source_method,
            "relationship_model": row.relationship_model,
            "qty": float(row.qty),
            "fifo_datetime": str(row.fifo_datetime),
            "receipt_name": row.receipt_name,
            "receipt_row_index": row.receipt_row_index,
        }
        for row in allocations
    ]


def _make_allocated_sales_invoice(
    frappe: Any,
    *,
    company: str,
    customer: str,
    allocations: list[AllocationSlice],
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

    for allocation in allocations:
        row = invoice.append(
            "items",
            {
                "item_code": ITEM_CODE,
                "item_name": "TP Gate 0B zero-value item",
                "description": f"Gate 0C {allocation.relationship_model} FIFO allocation",
                "warehouse": allocation.warehouse,
                "qty": float(allocation.qty),
                "uom": "Nos",
                "stock_uom": "Nos",
                "conversion_factor": 1,
                "rate": 100,
                "price_list_rate": 100,
                "income_account": company_doc.default_income_account,
                "expense_account": company_doc.default_expense_account,
                "cost_center": company_doc.cost_center,
                "allow_zero_valuation_rate": int(allocation.relationship_model != "OWN"),
            },
        )
        row.set(DIMENSION_FIELD, allocation.lot_name)

    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _balances(frappe: Any, warehouses: dict[str, str]) -> dict[str, float]:
    return {
        model: _dimension_balance(frappe, ITEM_CODE, warehouse, LOT_NAMES[model])
        for model, warehouse in warehouses.items()
    }


def run_global_fifo_flow(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Verify technical warehouses, zero valuation, global FIFO and cancel/reuse."""
    import frappe
    from frappe.utils import nowdate

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")

    _ensure_reference_doctype(frappe)
    dimension = _ensure_dimension(frappe)
    _ensure_item(frappe)
    customer = _ensure_customer(frappe)
    location, warehouses = _ensure_location_warehouses(frappe, company)
    for lot_name in LOT_NAMES.values():
        _ensure_lot_value(frappe, lot_name)
    frappe.db.commit()

    submitted: list[Any] = []
    result: dict[str, Any] = {
        "site": frappe.local.site,
        "company": company,
        "dimension": dimension.name,
        "location": location,
        "warehouses": warehouses,
        "warehouse_types": {
            model: frappe.db.get_value("Warehouse", warehouse, WAREHOUSE_FIELD)
            for model, warehouse in warehouses.items()
        },
        "lots": LOT_NAMES,
    }

    cancelled_sales_invoices = []
    try:
        receipts = {}
        for model in ["COMMISSION", "OWN", "CONSIGNMENT"]:
            receipt = _make_stock_entry(
                item_code=ITEM_CODE,
                company=company,
                warehouse=warehouses[model],
                qty=2,
                inward=True,
                dimension_value=LOT_NAMES[model],
                rate=RECEIPT_RATES[model],
                posting_date=nowdate(),
                posting_time=RECEIPT_TIMES[model],
            )
            submitted.append(receipt)
            receipts[model] = receipt

        result["receipt_vouchers"] = {model: document.name for model, document in receipts.items()}
        result["receipt_stock_ledger"] = _ledger_evidence(
            frappe, [document.name for document in receipts.values()]
        )

        candidates = _load_candidates(frappe, location=location, warehouses=warehouses)
        allocations = allocate_global_fifo(
            candidates,
            item_code=ITEM_CODE,
            location=location,
            qty=Decimal("5"),
            allowed_warehouses=frozenset(warehouses.values()),
        )
        result["allocation_preview"] = _allocation_evidence(allocations)

        invoice = _make_allocated_sales_invoice(
            frappe,
            company=company,
            customer=customer,
            allocations=allocations,
        )
        submitted.append(invoice)
        result["sales_invoice"] = invoice.name
        result["sales_invoice_stock_ledger"] = _ledger_evidence(frappe, [invoice.name])
        result["sales_invoice_gl"] = _gl_evidence(frappe, invoice.name)
        invoice.cancel()
        cancelled_sales_invoices.append(invoice.name)

        candidates_after_cancel = _load_candidates(frappe, location=location, warehouses=warehouses)
        retry_allocations = allocate_global_fifo(
            candidates_after_cancel,
            item_code=ITEM_CODE,
            location=location,
            qty=Decimal("1"),
            allowed_warehouses=frozenset(warehouses.values()),
        )
        result["allocation_after_cancel"] = _allocation_evidence(retry_allocations)
        retry_invoice = _make_allocated_sales_invoice(
            frappe,
            company=company,
            customer=customer,
            allocations=retry_allocations,
        )
        submitted.append(retry_invoice)
        result["retry_sales_invoice"] = retry_invoice.name
        result["retry_stock_ledger"] = _ledger_evidence(frappe, [retry_invoice.name])
        retry_invoice.cancel()
        cancelled_sales_invoices.append(retry_invoice.name)
        result["balances_after_cancel_reuse"] = _balances(frappe, warehouses)
    finally:
        result["cancelled_sales_invoices"] = cancelled_sales_invoices
        result["cancelled_receipts"] = _cancel_submitted(submitted)
        result["balances_after_cleanup"] = _balances(frappe, warehouses)

    expected_models = ["COMMISSION", "OWN", "CONSIGNMENT"]
    expected_qty = [2.0, 2.0, 1.0]
    if [row["relationship_model"] for row in result["allocation_preview"]] != expected_models:
        raise AssertionError(f"Global FIFO order is incorrect: {result}")
    if [row["qty"] for row in result["allocation_preview"]] != expected_qty:
        raise AssertionError(f"Global FIFO quantities are incorrect: {result}")
    if result["allocation_after_cancel"][0]["relationship_model"] != "COMMISSION":
        raise AssertionError(f"Cancelled allocation did not become FIFO-eligible again: {result}")

    receipt_ledger = {row[DIMENSION_FIELD]: row for row in result["receipt_stock_ledger"]}
    for model in ["COMMISSION", "CONSIGNMENT"]:
        row = receipt_ledger[LOT_NAMES[model]]
        if float(row["valuation_rate"] or 0) != 0 or float(row["stock_value_difference"] or 0) != 0:
            raise AssertionError(f"Expected zero-valued third-party receipt: {result}")

    sale_ledger = {row[DIMENSION_FIELD]: row for row in result["sales_invoice_stock_ledger"]}
    for model in ["COMMISSION", "CONSIGNMENT"]:
        if float(sale_ledger[LOT_NAMES[model]]["stock_value_difference"] or 0) != 0:
            raise AssertionError(f"Third-party sale changed stock value: {result}")
    if float(sale_ledger[LOT_NAMES["OWN"]]["stock_value_difference"] or 0) != -100:
        raise AssertionError(f"Own-stock COGS did not remain isolated: {result}")
    if result["balances_after_cancel_reuse"] != {model: 2.0 for model in expected_models}:
        raise AssertionError(f"Cancel/reuse changed available stock: {result}")
    if any(result["balances_after_cleanup"].values()):
        raise AssertionError(f"Expected zero balances after Gate 0C cleanup: {result}")

    return result


def _ensure_reservation_doctype(frappe: Any) -> None:
    if frappe.db.exists("DocType", RESERVATION_DOCTYPE):
        return

    frappe.get_doc(
        {
            "doctype": "DocType",
            "name": RESERVATION_DOCTYPE,
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
                },
                {
                    "fieldname": "available_qty",
                    "label": "Available Qty",
                    "fieldtype": "Float",
                    "reqd": 1,
                },
                {
                    "fieldname": "reserved_qty",
                    "label": "Reserved Qty",
                    "fieldtype": "Float",
                    "reqd": 1,
                },
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


def prepare_last_unit_probe(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Reset the persistent test-only row used by two concurrent processes."""
    import frappe

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")
    _ensure_reservation_doctype(frappe)
    if not frappe.db.exists(RESERVATION_DOCTYPE, RESERVATION_LOT):
        frappe.get_doc(
            {
                "doctype": RESERVATION_DOCTYPE,
                "lot_id": RESERVATION_LOT,
                "available_qty": 1,
                "reserved_qty": 0,
            }
        ).insert(ignore_permissions=True)
    frappe.db.set_value(
        RESERVATION_DOCTYPE,
        RESERVATION_LOT,
        {"available_qty": 1, "reserved_qty": 0},
        update_modified=False,
    )
    frappe.db.commit()
    return {"lot": RESERVATION_LOT, "available_qty": 1.0, "reserved_qty": 0.0}


def attempt_last_unit_reservation(
    confirm_site: str,
    confirm_write: str,
    contender: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Atomically reserve one unit; intended for two independent processes."""
    import frappe

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    sleep(2)
    frappe.db.sql(
        f"""
        UPDATE `tab{RESERVATION_DOCTYPE}`
        SET reserved_qty = reserved_qty + 1
        WHERE name = %s
          AND available_qty - reserved_qty >= 1
        """,
        (RESERVATION_LOT,),
    )
    affected_rows = int(frappe.db.sql("SELECT ROW_COUNT()")[0][0])
    state = frappe.db.get_value(
        RESERVATION_DOCTYPE,
        RESERVATION_LOT,
        ["available_qty", "reserved_qty"],
        as_dict=True,
    )
    frappe.db.commit()
    return {
        "contender": contender,
        "success": affected_rows == 1,
        "affected_rows": affected_rows,
        "available_qty": float(state.available_qty or 0),
        "reserved_qty": float(state.reserved_qty or 0),
    }


def cleanup_last_unit_probe(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Release the test-only reservation after the concurrency probe."""
    import frappe

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.db.set_value(
        RESERVATION_DOCTYPE,
        RESERVATION_LOT,
        "reserved_qty",
        0,
        update_modified=False,
    )
    frappe.db.commit()
    return {"lot": RESERVATION_LOT, "reserved_qty": 0.0}
