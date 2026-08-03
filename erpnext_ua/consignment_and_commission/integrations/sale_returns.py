"""Exact-lot managed Sales Invoice returns and financial reversals."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
from typing import Any

from ..doctype.cc_sale_return_allocation.cc_sale_return_allocation import WRITE_FLAG
from ..services.sale_return import (
    ManagedReturnError,
    ManagedReturnRequest,
    calculate_return_financial_delta,
    managed_return_fingerprint,
    validate_return_request,
)
from ..setup.ownership_dimension import (
    MANAGED_RETURN_FIELD,
    OWNERSHIP_FIELD,
    POSTING_KIND_FIELD,
    RELATIONSHIP_MODEL_FIELD,
    RETURN_FINGERPRINT_FIELD,
    RETURN_IDEMPOTENCY_FIELD,
    RETURN_SALE_ALLOCATION_FIELD,
    SALES_INVOICE_FIELD,
    SOURCE_METHOD_FIELD,
)
from .sale_allocations import _recognition_cancellation, get_account_mapping
from .tracking import get_tracking_selections


@contextmanager
def _return_write(frappe: Any):
    previous = getattr(frappe.flags, WRITE_FLAG, False)
    setattr(frappe.flags, WRITE_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, WRITE_FLAG, previous)


def _existing_return(frappe: Any, request: ManagedReturnRequest, fingerprint: str) -> Any | None:
    name = frappe.db.get_value(
        "Sales Invoice",
        {RETURN_IDEMPOTENCY_FIELD: request.idempotency_key},
        "name",
    )
    if not name:
        return None
    invoice = frappe.get_doc("Sales Invoice", name)
    if invoice.get(RETURN_FINGERPRINT_FIELD) != fingerprint:
        raise ManagedReturnError(
            f"Return idempotency key {request.idempotency_key!r} belongs to another request"
        )
    return invoice


def _validate_reported_return(frappe: Any, sale: Any) -> None:
    if not sale.settlement_report:
        return
    report = frappe.db.get_value(
        "CC Settlement Report",
        sale.settlement_report,
        ["docstatus", "status"],
        as_dict=True,
    )
    if not report or report.docstatus != 1 or report.status == "CANCELLED":
        raise ManagedReturnError(
            f"CC Sale Allocation {sale.name} has no active historical Settlement Report"
        )


def create_return_invoice(
    request: ManagedReturnRequest,
    *,
    invoice_values: dict[str, Any] | None = None,
    allow_transaction_writes: bool = False,
) -> Any:
    """Create one idempotent exact-source return for a managed Sales Invoice."""
    import frappe
    from frappe.utils import getdate

    if frappe.db.transaction_writes and not allow_transaction_writes:
        raise ManagedReturnError(
            "Managed return creation must start before unrelated transaction writes"
        )
    validate_return_request(request)
    fingerprint = managed_return_fingerprint(request)
    existing = _existing_return(frappe, request, fingerprint)
    if existing:
        return existing
    names = tuple(line.sale_allocation for line in request.lines)
    placeholders = ", ".join(["%s"] * len(names))
    locked = frappe.db.sql(
        f"select name from `tabCC Sale Allocation` where name in ({placeholders}) "
        "order by name for update",
        tuple(sorted(names)),
        as_dict=True,
    )
    if {row.name for row in locked} != set(names):
        raise ManagedReturnError("One or more CC Sale Allocations do not exist")
    sales = {name: frappe.get_doc("CC Sale Allocation", name) for name in names}
    first = sales[names[0]]
    if any(sale.sales_invoice != first.sales_invoice for sale in sales.values()):
        raise ManagedReturnError("One return cannot mix original Sales Invoices")
    original = frappe.get_doc("Sales Invoice", first.sales_invoice)
    if original.docstatus != 1:
        raise ManagedReturnError("Original managed Sales Invoice must be submitted")
    if getdate(request.posting_date) < getdate(original.posting_date):
        raise ManagedReturnError("Return date cannot be before the original sale date")

    invoice = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "company": original.company,
            "customer": original.customer,
            "posting_date": request.posting_date,
            "due_date": request.posting_date,
            "is_return": 1,
            "return_against": original.name,
            "update_stock": 1,
            "currency": original.currency,
            "conversion_rate": original.conversion_rate,
            "debit_to": original.debit_to,
            MANAGED_RETURN_FIELD: 1,
            RETURN_IDEMPOTENCY_FIELD: request.idempotency_key,
            RETURN_FINGERPRINT_FIELD: fingerprint,
        }
    )
    invoice.update(_allowed_invoice_values(invoice_values or {}))
    original_rows = {row.name: row for row in original.items}
    for line in request.lines:
        sale = sales[line.sale_allocation]
        if sale.status not in {"SOLD", "PARTIALLY_RETURNED", "REPORTED"}:
            raise ManagedReturnError(
                f"CC Sale Allocation {sale.name} is cancelled or already returned"
            )
        _validate_reported_return(frappe, sale)
        qty = Decimal(str(line.qty))
        remaining = Decimal(str(sale.sold_qty)) - Decimal(str(sale.returned_qty or 0))
        if qty > remaining:
            raise ManagedReturnError(f"Return quantity exceeds {sale.name} remaining sold quantity")
        if sale.serial_no and qty != 1:
            raise ManagedReturnError("Serial-number return quantity must equal one")
        source = original_rows.get(sale.sales_invoice_item)
        if not source:
            raise ManagedReturnError(f"CC Sale Allocation {sale.name} lost its original invoice row")
        financials = calculate_return_financial_delta(
            relationship_model=sale.relationship_model,
            sold_qty=sale.sold_qty,
            returned_qty_before=sale.returned_qty or 0,
            return_qty=qty,
            gross_amount=sale.net_amount,
            commission_amount=sale.commission_amount or 0,
            partner_amount=sale.partner_amount or 0,
            currency_precision=int(source.precision("net_amount") or 2),
        )
        return_rate = financials.gross_amount / qty
        row = invoice.append(
            "items",
            {
                "item_code": sale.item_code,
                "item_name": source.item_name,
                "description": source.description,
                "warehouse": sale.warehouse,
                "qty": -qty,
                "uom": source.uom,
                "stock_uom": source.stock_uom,
                "conversion_factor": source.conversion_factor,
                "rate": return_rate,
                "price_list_rate": return_rate,
                "income_account": source.income_account,
                "expense_account": source.expense_account,
                "cost_center": source.cost_center,
                "sales_invoice_item": source.name,
                "ua_pos_order_item": source.get("ua_pos_order_item"),
                "use_serial_batch_fields": int(bool(sale.serial_no or sale.batch_no)),
                "serial_no": sale.serial_no,
                "batch_no": sale.batch_no,
                RETURN_SALE_ALLOCATION_FIELD: sale.name,
                SOURCE_METHOD_FIELD: sale.source_method,
                RELATIONSHIP_MODEL_FIELD: sale.relationship_model,
            },
        )
        row.set(OWNERSHIP_FIELD, sale.stock_lot)
    payments = (invoice_values or {}).get("payments")
    if payments is not None:
        invoice.set("payments", payments)
        invoice.run_method("calculate_taxes_and_totals")
    if invoice.get("ua_pos_order"):
        order = frappe.get_doc("POS Order", invoice.ua_pos_order)
        from erpnext_ua.ua_gift_certificates.adapters.sales_invoice import (
            prepare_invoice as prepare_gift,
        )
        from erpnext_ua.ua_loyalty.adapters.sales_invoice import (
            prepare_invoice as prepare_loyalty,
        )

        prepare_loyalty(invoice, order)
        prepare_gift(invoice, order)
    name = "CC-RET-" + sha256(
        f"{request.idempotency_key}:{fingerprint}".encode()
    ).hexdigest()[:20].upper()
    savepoint = "cc_managed_return_create"
    frappe.db.savepoint(savepoint)
    try:
        invoice.insert(ignore_permissions=True, set_name=name)
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=savepoint)
        existing = _existing_return(frappe, request, fingerprint)
        if existing:
            return existing
        raise ManagedReturnError("Concurrent managed return creation did not settle") from None
    except frappe.QueryDeadlockError:
        frappe.db.rollback()
        existing = _existing_return(frappe, request, fingerprint)
        if existing:
            return existing
        raise ManagedReturnError("Concurrent managed return database conflict did not settle") from None
    return invoice


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


def _tracking_names(frappe: Any, row: Any, doctype: str) -> set[str]:
    return {
        selection.name
        for selection in get_tracking_selections(frappe, row)
        if selection.doctype == doctype
    }


def validate_return_invoice(doc: Any) -> None:
    import frappe
    from frappe.utils import getdate

    if not doc.get(MANAGED_RETURN_FIELD) or not doc.is_return or not doc.return_against:
        frappe.throw("CC stock returns require the dedicated managed return workflow")
    original = frappe.get_doc("Sales Invoice", doc.return_against)
    if original.docstatus != 1 or getdate(doc.posting_date) < getdate(original.posting_date):
        frappe.throw("Managed return requires a submitted original invoice and valid date")
    parent_expected = {
        "company": original.company,
        "customer": original.customer,
        "currency": original.currency,
        "conversion_rate": original.conversion_rate,
        "debit_to": original.debit_to,
        "update_stock": 1,
    }
    parent_mismatches = [
        fieldname
        for fieldname, value in parent_expected.items()
        if str(doc.get(fieldname) or "") != str(value or "")
    ]
    if parent_mismatches:
        frappe.throw(
            "Managed return header changed: "
            f"{', '.join(dict.fromkeys(parent_mismatches))}"
        )
    original_rows = {row.name: row for row in original.items}
    for row in doc.items:
        sale_name = row.get(RETURN_SALE_ALLOCATION_FIELD)
        sale = frappe.get_doc("CC Sale Allocation", sale_name) if sale_name else None
        if not sale or sale.sales_invoice != original.name:
            frappe.throw(f"Row {row.idx}: original CC Sale Allocation is not returnable")
        try:
            _validate_reported_return(frappe, sale)
        except ManagedReturnError as exc:
            frappe.throw(str(exc))
        source = original_rows.get(sale.sales_invoice_item)
        if not source:
            frappe.throw(f"Row {row.idx}: original Sales Invoice row is missing")
        expected = {
            "item_code": sale.item_code,
            "warehouse": sale.warehouse,
            OWNERSHIP_FIELD: sale.stock_lot,
            SOURCE_METHOD_FIELD: sale.source_method,
            RELATIONSHIP_MODEL_FIELD: sale.relationship_model,
            "sales_invoice_item": sale.sales_invoice_item,
            "income_account": source.income_account,
            "expense_account": source.expense_account,
            "cost_center": source.cost_center,
            "stock_uom": source.stock_uom,
            "conversion_factor": source.conversion_factor,
        }
        mismatches = [
            fieldname
            for fieldname, value in expected.items()
            if str(row.get(fieldname) or "") != str(value or "")
        ]
        qty = abs(Decimal(str(row.stock_qty or 0)))
        remaining = Decimal(str(sale.sold_qty)) - Decimal(str(sale.returned_qty or 0))
        if Decimal(str(row.stock_qty or 0)) >= 0 or qty <= 0 or qty > remaining:
            mismatches.append("stock_qty")
        try:
            financials = _returned_financials(row, sale)
        except ManagedReturnError as exc:
            frappe.throw(str(exc))
        if abs(Decimal(str(row.net_amount or 0))) != financials.gross_amount:
            mismatches.append("net_amount")
        try:
            base_financials = _returned_base_financials(row, sale)
        except ManagedReturnError as exc:
            frappe.throw(str(exc))
        if abs(Decimal(str(row.base_net_amount or 0))) != base_financials.gross_amount:
            mismatches.append("base_net_amount")
        if _tracking_names(frappe, row, "Serial No") != (
            {sale.serial_no} if sale.serial_no else set()
        ):
            mismatches.append("serial_no")
        if _tracking_names(frappe, row, "Batch") != ({sale.batch_no} if sale.batch_no else set()):
            mismatches.append("batch_no")
        if mismatches:
            frappe.throw(
                f"Row {row.idx}: managed return source changed: "
                f"{', '.join(dict.fromkeys(mismatches))}"
            )


def _returned_financials(row: Any, sale: Any) -> Any:
    return calculate_return_financial_delta(
        relationship_model=sale.relationship_model,
        sold_qty=sale.sold_qty,
        returned_qty_before=sale.returned_qty or 0,
        return_qty=abs(Decimal(str(row.stock_qty))),
        gross_amount=sale.net_amount,
        commission_amount=sale.commission_amount or 0,
        partner_amount=sale.partner_amount or 0,
        currency_precision=int(row.precision("net_amount") or 2),
    )


def _returned_base_financials(row: Any, sale: Any) -> Any:
    return calculate_return_financial_delta(
        relationship_model=sale.relationship_model,
        sold_qty=sale.sold_qty,
        returned_qty_before=sale.returned_qty or 0,
        return_qty=abs(Decimal(str(row.stock_qty))),
        gross_amount=sale.base_net_amount,
        commission_amount=sale.base_commission_amount or 0,
        partner_amount=sale.base_partner_amount or 0,
        currency_precision=int(row.precision("base_net_amount") or 2),
    )


def post_return_allocations_and_reversal(doc: Any) -> None:
    import frappe

    sale_names = tuple(
        sorted(row.get(RETURN_SALE_ALLOCATION_FIELD) for row in doc.items)
    )
    placeholders = ", ".join(["%s"] * len(sale_names))
    locked = frappe.db.sql(
        f"select name from `tabCC Sale Allocation` where name in ({placeholders}) "
        "order by name for update",
        sale_names,
        as_dict=True,
    )
    if {row.name for row in locked} != set(sale_names):
        frappe.throw("One or more return CC Sale Allocations no longer exist")
    audits = []
    for row in doc.items:
        sale = frappe.get_doc("CC Sale Allocation", row.get(RETURN_SALE_ALLOCATION_FIELD))
        qty = abs(Decimal(str(row.stock_qty)))
        new_returned = Decimal(str(sale.returned_qty or 0)) + qty
        if new_returned > Decimal(str(sale.sold_qty)):
            frappe.throw(f"CC Sale Allocation {sale.name} is no longer returnable")
        try:
            _validate_reported_return(frappe, sale)
        except ManagedReturnError as exc:
            frappe.throw(str(exc))
        financials = _returned_financials(row, sale)
        base_financials = _returned_base_financials(row, sale)
        if abs(Decimal(str(row.net_amount or 0))) != financials.gross_amount:
            frappe.throw(f"CC Sale Allocation {sale.name} return amount changed before submit")
        if abs(Decimal(str(row.base_net_amount or 0))) != base_financials.gross_amount:
            frappe.throw(f"CC Sale Allocation {sale.name} base return amount changed before submit")
        name = "CC-RET-ALLOC-" + sha256(f"{doc.name}:{row.name}".encode()).hexdigest()[:20].upper()
        audit = frappe.get_doc(
            {
                "doctype": "CC Sale Return Allocation",
                "status": "RETURNED",
                "return_sales_invoice": doc.name,
                "return_sales_invoice_item": row.name,
                "sale_allocation": sale.name,
                "original_sales_invoice": sale.sales_invoice,
                "posting_date": doc.posting_date,
                "company": sale.company,
                "customer": sale.customer,
                "item_code": sale.item_code,
                "stock_lot": sale.stock_lot,
                "warehouse": sale.warehouse,
                "source_method": sale.source_method,
                "relationship_model": sale.relationship_model,
                "currency": sale.currency,
                "returned_qty": qty,
                "net_amount": financials.gross_amount,
                "commission_amount": financials.commission_amount,
                "partner_amount": financials.partner_amount,
                "retained_amount": financials.retained_amount,
                "base_net_amount": base_financials.gross_amount,
                "base_commission_amount": base_financials.commission_amount,
                "base_partner_amount": base_financials.partner_amount,
                "base_retained_amount": base_financials.retained_amount,
                "settlement_report": sale.settlement_report,
            }
        )
        with _return_write(frappe):
            audit.insert(ignore_permissions=True, set_name=name)
        status = (
            "REPORTED"
            if sale.settlement_report
            else "RETURNED"
            if new_returned == Decimal(str(sale.sold_qty))
            else "PARTIALLY_RETURNED"
        )
        frappe.db.set_value(
            "CC Sale Allocation",
            sale.name,
            {"returned_qty": new_returned, "status": status},
            update_modified=False,
        )
        audits.append(audit)
    journal = _post_reversal_journal(frappe, doc, audits)
    if journal:
        for audit in audits:
            frappe.db.set_value(
                "CC Sale Return Allocation",
                audit.name,
                "reversal_journal_entry",
                journal.name,
                update_modified=False,
            )
    from .off_balance import post_return_off_balance

    post_return_off_balance(doc, audits)
    from .settlement_adjustments import create_return_settlement_adjustments

    create_return_settlement_adjustments(doc, audits)


def _post_reversal_journal(frappe: Any, doc: Any, audits: list[Any]) -> Any | None:
    third_party = [row for row in audits if row.relationship_model != "OWN"]
    if not third_party:
        return None
    mapping = get_account_mapping(frappe, doc.company)
    company = frappe.get_cached_doc("Company", doc.company)
    totals: dict[str, list[Decimal]] = {}

    def add(account: str, debit: Decimal = Decimal("0"), credit: Decimal = Decimal("0")) -> None:
        values = totals.setdefault(account, [Decimal("0"), Decimal("0")])
        values[0] += debit
        values[1] += credit

    for row in third_party:
        partner = Decimal(str(row.base_partner_amount or 0))
        retained = Decimal(str(row.base_retained_amount or 0))
        add(mapping.principal_proceeds_deduction_account, credit=partner)
        if retained:
            add(mapping.gross_proceeds_clearing_account, credit=retained)
            add(
                mapping.commission_revenue_account,
                debit=retained,
            )
        if row.relationship_model == "COMMISSION":
            add(mapping.unreported_commission_liability_account, debit=partner)
        else:
            add(mapping.unreported_consignment_liability_account, debit=partner)
    debit = sum((value[0] for value in totals.values()), Decimal("0"))
    credit = sum((value[1] for value in totals.values()), Decimal("0"))
    if debit != credit:
        frappe.throw(f"Managed return reversal is not balanced: debit={debit}, credit={credit}")
    journal = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "company": doc.company,
            "posting_date": doc.posting_date,
            "voucher_type": "Journal Entry",
            "user_remark": f"CC managed return reversal for {doc.name}",
            SALES_INVOICE_FIELD: doc.name,
            POSTING_KIND_FIELD: "RETURN_REVERSAL",
        }
    )
    for account, (debit_amount, credit_amount) in totals.items():
        journal.append(
            "accounts",
            {
                "account": account,
                "debit_in_account_currency": debit_amount,
                "credit_in_account_currency": credit_amount,
                "exchange_rate": 1,
                "cost_center": company.cost_center,
            },
        )
    name = "CC-RET-REV-" + sha256(doc.name.encode()).hexdigest()[:20].upper()
    journal.insert(ignore_permissions=True, set_name=name)
    journal.submit()
    return journal


def cancel_return_reversal(doc: Any) -> None:
    import frappe

    from .settlement_adjustments import cancel_return_settlement_adjustments

    cancel_return_settlement_adjustments(doc.name)

    name = frappe.db.get_value(
        "Journal Entry",
        {SALES_INVOICE_FIELD: doc.name, POSTING_KIND_FIELD: "RETURN_REVERSAL"},
        "name",
    )
    if name:
        journal = frappe.get_doc("Journal Entry", name)
        if journal.docstatus == 1:
            with _recognition_cancellation(frappe):
                journal.cancel()


def cancel_return_allocations(doc: Any) -> None:
    import frappe

    audits = frappe.get_all(
        "CC Sale Return Allocation",
        filters={"return_sales_invoice": doc.name, "status": "RETURNED"},
        fields=["name", "sale_allocation", "returned_qty"],
        order_by="sale_allocation asc",
    )
    if audits:
        sale_names = tuple(audit.sale_allocation for audit in audits)
        placeholders = ", ".join(["%s"] * len(sale_names))
        frappe.db.sql(
            f"select name from `tabCC Sale Allocation` where name in ({placeholders}) "
            "order by name for update",
            sale_names,
        )
    for audit in audits:
        sale = frappe.get_doc("CC Sale Allocation", audit.sale_allocation)
        returned = Decimal(str(sale.returned_qty or 0)) - Decimal(str(audit.returned_qty))
        if returned < 0:
            frappe.throw(f"CC Sale Allocation {sale.name} return balance is corrupted")
        status = (
            "REPORTED"
            if sale.settlement_report
            else "PARTIALLY_RETURNED"
            if returned
            else "SOLD"
        )
        frappe.db.set_value(
            "CC Sale Allocation",
            sale.name,
            {"returned_qty": returned, "status": status},
            update_modified=False,
        )
        frappe.db.set_value(
            "CC Sale Return Allocation",
            audit.name,
            "status",
            "CANCELLED",
            update_modified=False,
        )
