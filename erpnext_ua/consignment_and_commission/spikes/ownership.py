"""Test-site-only ownership conversion, purchase and partner-return probes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from ..services.ownership import OwnershipDispositionRequest, plan_ownership_disposition
from .accounting import (
    _ensure_accounts,
    _ensure_supplier,
    _exchange_journal_evidence,
    _gl_evidence,
)
from .fifo import _ensure_location_warehouses
from .inventory_dimension import (
    DIMENSION_FIELD,
    ITEM_CODE,
    _assert_test_scope,
    _dimension_balance,
    _ensure_customer,
    _ensure_dimension,
    _ensure_item,
    _ensure_lot_value,
    _ensure_reference_doctype,
    _ledger_evidence,
    _make_stock_entry,
)
from .serial_batch import (
    SERIAL_ITEM_CODE,
    _bundle_evidence,
    _ensure_tracked_item,
    _ensure_tracking_owner_fields,
    _reload_bundle,
    _set_tracking_owner,
    _validate_draft_tracking_ownership,
)

EVENT_FIELD = "tp_spike_ownership_event"
SUPPLIER_NAMES = {
    "UAH": "TP Gate 0F Supplier UAH",
    "USD": "TP Gate 0F Supplier USD",
}


def _ensure_event_fields(frappe: Any) -> None:
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            doctype: [
                {
                    "fieldname": EVENT_FIELD,
                    "label": "TP Spike Ownership Event",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "search_index": 1,
                }
            ]
            for doctype in ["Stock Entry", "Purchase Invoice", "Payment Entry", "Sales Invoice"]
        }
    )


def _tag(document: Any, event_id: str) -> Any:
    document.set(EVENT_FIELD, event_id)
    document.db_set(EVENT_FIELD, event_id, update_modified=False)
    return document


def _make_purchase_invoice(
    frappe: Any,
    *,
    company: str,
    supplier: str,
    payable_account: str,
    item_code: str,
    warehouse: str,
    lot_id: str,
    qty: Decimal,
    unit_cost: Decimal,
    currency: str,
    exchange_rate: Decimal,
    posting_date: str,
    event_id: str,
) -> Any:
    company_doc = frappe.get_cached_doc("Company", company)
    invoice = frappe.new_doc("Purchase Invoice")
    invoice.company = company
    invoice.supplier = supplier
    invoice.posting_date = posting_date
    invoice.due_date = posting_date
    invoice.bill_date = posting_date
    invoice.bill_no = event_id
    invoice.update_stock = 1
    invoice.currency = currency
    invoice.conversion_rate = float(exchange_rate)
    invoice.credit_to = payable_account
    invoice.set(EVENT_FIELD, event_id)
    row = invoice.append(
        "items",
        {
            "item_code": item_code,
            "item_name": item_code,
            "description": f"Gate 0F ownership conversion {event_id}",
            "warehouse": warehouse,
            "qty": float(qty),
            "uom": "Nos",
            "stock_uom": "Nos",
            "conversion_factor": 1,
            "rate": float(unit_cost),
            "price_list_rate": float(unit_cost),
            "expense_account": company_doc.default_expense_account,
            "cost_center": company_doc.cost_center,
        },
    )
    row.set(DIMENSION_FIELD, lot_id)
    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _make_purchase_payment(
    *,
    invoice: Any,
    obligation_amount: Decimal,
    bank_amount: Decimal,
    posting_date: str,
    sequence: int,
    event_id: str,
) -> Any:
    import frappe
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    payment = get_payment_entry(
        "Purchase Invoice",
        invoice.name,
        party_amount=float(obligation_amount),
        bank_amount=float(bank_amount),
        bank_account=frappe.get_cached_value("Company", invoice.company, "default_bank_account"),
        reference_date=posting_date,
    )
    payment.posting_date = posting_date
    payment.reference_no = f"{event_id}-PAY-{sequence}"
    payment.reference_date = posting_date
    payment.set(EVENT_FIELD, event_id)
    reference = payment.references[0]
    invoice.reload()
    reference.total_amount = invoice.grand_total
    reference.outstanding_amount = invoice.outstanding_amount
    reference.allocated_amount = float(obligation_amount)
    payment.insert(ignore_permissions=True)
    payment.submit()
    return payment


def _make_converted_sale(
    frappe: Any,
    *,
    company: str,
    customer: str,
    warehouse: str,
    lot_id: str,
    qty: Decimal,
    rate: Decimal,
    posting_date: str,
    event_id: str,
) -> Any:
    company_doc = frappe.get_cached_doc("Company", company)
    invoice = frappe.new_doc("Sales Invoice")
    invoice.company = company
    invoice.customer = customer
    invoice.posting_date = posting_date
    invoice.due_date = posting_date
    invoice.update_stock = 1
    invoice.currency = company_doc.default_currency
    invoice.conversion_rate = 1
    invoice.debit_to = company_doc.default_receivable_account
    invoice.set(EVENT_FIELD, event_id)
    row = invoice.append(
        "items",
        {
            "item_code": ITEM_CODE,
            "item_name": ITEM_CODE,
            "description": f"Gate 0F converted own stock sale {event_id}",
            "warehouse": warehouse,
            "qty": float(qty),
            "uom": "Nos",
            "stock_uom": "Nos",
            "conversion_factor": 1,
            "rate": float(rate),
            "price_list_rate": float(rate),
            "income_account": company_doc.default_income_account,
            "expense_account": company_doc.default_expense_account,
            "cost_center": company_doc.cost_center,
        },
    )
    row.set(DIMENSION_FIELD, lot_id)
    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _active_gl(frappe: Any, doctype: str, voucher_name: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "GL Entry",
        filters={"voucher_type": doctype, "voucher_no": voucher_name, "is_cancelled": 0},
        fields=[
            "account",
            "party_type",
            "party",
            "account_currency",
            "debit",
            "credit",
            "debit_in_account_currency",
            "credit_in_account_currency",
            "against_voucher_type",
            "against_voucher",
        ],
        order_by="creation asc, name asc",
    )


def _net_exchange_difference(
    payment_name: str,
    *,
    payment_gl: list[dict[str, Any]],
    exchange_journals: list[dict[str, Any]],
    exchange_account: str,
) -> float:
    rows = [row for row in payment_gl if row["account"] == exchange_account]
    for journal in exchange_journals:
        rows.extend(
            row
            for row in journal["gl"]
            if row["account"] == exchange_account and row.get("against_voucher") == payment_name
        )
    return round(sum(float(row.get("debit") or 0) - float(row.get("credit") or 0) for row in rows), 2)


def _cancel_submitted(documents: list[Any]) -> tuple[list[str], list[str]]:
    cancelled = []
    errors = []
    for document in reversed(documents):
        try:
            document.reload()
            if document.docstatus == 1:
                document.cancel()
                cancelled.append(f"{document.doctype} {document.name}")
        except Exception as exc:  # pragma: no cover - returned as integration evidence
            errors.append(f"{document.doctype} {document.name}: {type(exc).__name__}: {exc}")
    return cancelled, errors


def _execute_conversion(
    frappe: Any,
    *,
    company: str,
    supplier: str,
    payable_account: str,
    source_warehouse: str,
    own_warehouse: str,
    relationship_model: str,
    source_lot: str,
    target_lot: str,
    available_qty: Decimal,
    convert_qty: Decimal,
    return_qty: Decimal,
    unit_cost: Decimal,
    currency: str,
    exchange_rate: Decimal,
    posting_date: str,
    event_id: str,
    submitted: list[Any],
) -> tuple[Any, dict[str, Any]]:
    company_currency = frappe.get_cached_value("Company", company, "default_currency")
    plan = plan_ownership_disposition(
        OwnershipDispositionRequest(
            event_id=event_id,
            item_code=ITEM_CODE,
            relationship_model=relationship_model,
            source_lot=source_lot,
            source_warehouse=source_warehouse,
            available_qty=available_qty,
            convert_qty=convert_qty,
            return_qty=return_qty,
            target_lot=target_lot,
            target_warehouse=own_warehouse,
            unit_cost=unit_cost,
            currency=currency,
            company_currency=company_currency,
            exchange_rate=exchange_rate,
        )
    )

    receipt = _make_stock_entry(
        item_code=ITEM_CODE,
        company=company,
        warehouse=source_warehouse,
        qty=float(available_qty),
        inward=True,
        dimension_value=source_lot,
        rate=0,
        posting_date=posting_date,
    )
    _tag(receipt, event_id)
    submitted.append(receipt)

    conversion_issue = _make_stock_entry(
        item_code=ITEM_CODE,
        company=company,
        warehouse=source_warehouse,
        qty=float(convert_qty),
        inward=False,
        dimension_value=source_lot,
        rate=0,
        posting_date=posting_date,
    )
    _tag(conversion_issue, event_id)
    submitted.append(conversion_issue)

    purchase_invoice = _make_purchase_invoice(
        frappe,
        company=company,
        supplier=supplier,
        payable_account=payable_account,
        item_code=ITEM_CODE,
        warehouse=own_warehouse,
        lot_id=target_lot,
        qty=convert_qty,
        unit_cost=unit_cost,
        currency=currency,
        exchange_rate=exchange_rate,
        posting_date=posting_date,
        event_id=event_id,
    )
    submitted.append(purchase_invoice)

    return_issue = None
    if return_qty:
        return_issue = _make_stock_entry(
            item_code=ITEM_CODE,
            company=company,
            warehouse=source_warehouse,
            qty=float(return_qty),
            inward=False,
            dimension_value=source_lot,
            rate=0,
            posting_date=posting_date,
        )
        _tag(return_issue, event_id)
        submitted.append(return_issue)

    purchase_invoice.reload()
    evidence = {
        "event_id": event_id,
        "plan": {
            "relationship_model": plan.relationship_model,
            "converted_qty": float(plan.converted_qty),
            "returned_qty": float(plan.returned_qty),
            "remaining_qty": float(plan.remaining_qty),
            "obligation_amount": float(plan.obligation_amount),
            "base_asset_value": float(plan.base_asset_value),
            "currency": plan.currency,
            "exchange_rate": float(plan.exchange_rate),
            "movements": [movement.kind for movement in plan.movements],
        },
        "documents": {
            "receipt": receipt.name,
            "conversion_issue": conversion_issue.name,
            "purchase_invoice": purchase_invoice.name,
            "partner_return_issue": return_issue.name if return_issue else None,
        },
        "source_balance": _dimension_balance(frappe, ITEM_CODE, source_warehouse, source_lot),
        "own_balance": _dimension_balance(frappe, ITEM_CODE, own_warehouse, target_lot),
        "purchase_outstanding": float(purchase_invoice.outstanding_amount or 0),
        "stock_ledger": _ledger_evidence(
            frappe,
            [
                receipt.name,
                conversion_issue.name,
                purchase_invoice.name,
                *([return_issue.name] if return_issue else []),
            ],
        ),
        "purchase_gl": _active_gl(frappe, "Purchase Invoice", purchase_invoice.name),
    }
    return purchase_invoice, evidence


def _run_serialized_return(
    frappe: Any,
    *,
    company: str,
    warehouse: str,
    source_lot: str,
    posting_date: str,
    event_id: str,
    submitted: list[Any],
) -> dict[str, Any]:
    serials = (f"{event_id}-SER-1", f"{event_id}-SER-2")
    plan = plan_ownership_disposition(
        OwnershipDispositionRequest(
            event_id=event_id,
            item_code=SERIAL_ITEM_CODE,
            relationship_model="COMMISSION",
            source_lot=source_lot,
            source_warehouse=warehouse,
            available_qty=Decimal("2"),
            return_qty=Decimal("1"),
            return_serial_numbers=(serials[0],),
        )
    )
    receipt = _make_stock_entry(
        item_code=SERIAL_ITEM_CODE,
        company=company,
        warehouse=warehouse,
        qty=2,
        inward=True,
        dimension_value=source_lot,
        serial_no="\n".join(serials),
        use_serial_batch_fields=True,
        rate=0,
        posting_date=posting_date,
    )
    _tag(receipt, event_id)
    submitted.append(receipt)
    receipt_bundle = _reload_bundle(receipt)
    for serial in serials:
        _set_tracking_owner(frappe, "Serial No", serial, source_lot)

    returned = _make_stock_entry(
        item_code=SERIAL_ITEM_CODE,
        company=company,
        warehouse=warehouse,
        qty=1,
        inward=False,
        dimension_value=source_lot,
        serial_no=serials[0],
        use_serial_batch_fields=True,
        before_submit=lambda document: _validate_draft_tracking_ownership(frappe, document),
        rate=0,
        posting_date=posting_date,
    )
    _tag(returned, event_id)
    submitted.append(returned)
    return_bundle = _reload_bundle(returned)

    return {
        "event_id": event_id,
        "plan": {
            "returned_qty": float(plan.returned_qty),
            "remaining_qty": float(plan.remaining_qty),
            "serial_numbers": list(plan.movements[0].serial_numbers),
        },
        "receipt": receipt.name,
        "return_issue": returned.name,
        "bundles": _bundle_evidence(frappe, [receipt_bundle, return_bundle]),
        "dimension_balance": _dimension_balance(frappe, SERIAL_ITEM_CODE, warehouse, source_lot),
        "serial_state": {
            serial: frappe.db.get_value(
                "Serial No",
                serial,
                ["warehouse", DIMENSION_FIELD],
                as_dict=True,
            )
            for serial in serials
        },
    }


def run_ownership_conversion_flow(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Verify conversion purchase, payouts, returns, valuation and cleanup."""
    import frappe
    from frappe.utils import nowdate

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")
    company_doc = frappe.get_cached_doc("Company", company)
    if company_doc.default_currency != "UAH":
        raise RuntimeError("Gate 0F fixture expects a UAH test Company")

    _ensure_reference_doctype(frappe)
    _ensure_dimension(frappe)
    _ensure_item(frappe)
    _ensure_customer(frappe)
    _ensure_event_fields(frappe)
    _ensure_tracking_owner_fields(frappe)
    _ensure_tracked_item(frappe, item_code=SERIAL_ITEM_CODE, batch=False, serial=True)
    _, warehouses = _ensure_location_warehouses(frappe, company)
    accounts = _ensure_accounts(frappe, company)
    suppliers = {
        "UAH": _ensure_supplier(
            frappe,
            company=company,
            supplier_name=SUPPLIER_NAMES["UAH"],
            payable_account=accounts["supplier_payable"],
        ),
        "USD": _ensure_supplier(
            frappe,
            company=company,
            supplier_name=SUPPLIER_NAMES["USD"],
            payable_account=accounts["supplier_payable_usd"],
        ),
    }

    run_id = uuid4().hex[:10].upper()
    posting_date = nowdate()
    lots = {
        "commission_uah_source": f"TP-GATE-0F-{run_id}-COM-UAH",
        "commission_uah_target": f"TP-GATE-0F-{run_id}-OWN-UAH",
        "commission_usd_source": f"TP-GATE-0F-{run_id}-COM-USD",
        "commission_usd_target": f"TP-GATE-0F-{run_id}-OWN-USD",
        "consignment_uah_source": f"TP-GATE-0F-{run_id}-CON-UAH",
        "consignment_uah_target": f"TP-GATE-0F-{run_id}-OWN-CON",
        "serialized_source": f"TP-GATE-0F-{run_id}-SER",
    }
    for lot_id in lots.values():
        _ensure_lot_value(frappe, lot_id)
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
        "run_id": run_id,
        "warehouses": warehouses,
        "lots": lots,
        "original_bundle_setting": original_bundle_setting,
    }

    try:
        commission_uah_invoice, commission_uah = _execute_conversion(
            frappe,
            company=company,
            supplier=suppliers["UAH"],
            payable_account=accounts["supplier_payable"],
            source_warehouse=warehouses["COMMISSION"],
            own_warehouse=warehouses["OWN"],
            relationship_model="COMMISSION",
            source_lot=lots["commission_uah_source"],
            target_lot=lots["commission_uah_target"],
            available_qty=Decimal("3"),
            convert_qty=Decimal("2"),
            return_qty=Decimal("1"),
            unit_cost=Decimal("80"),
            currency="UAH",
            exchange_rate=Decimal("1"),
            posting_date=posting_date,
            event_id=f"TP-G0F-{run_id}-COM-UAH",
            submitted=submitted,
        )
        commission_uah_payment = _make_purchase_payment(
            invoice=commission_uah_invoice,
            obligation_amount=Decimal("160"),
            bank_amount=Decimal("160"),
            posting_date=posting_date,
            sequence=1,
            event_id=commission_uah["event_id"],
        )
        submitted.append(commission_uah_payment)
        converted_sale = _make_converted_sale(
            frappe,
            company=company,
            customer=_ensure_customer(frappe),
            warehouse=warehouses["OWN"],
            lot_id=lots["commission_uah_target"],
            qty=Decimal("1"),
            rate=Decimal("120"),
            posting_date=posting_date,
            event_id=commission_uah["event_id"],
        )
        submitted.append(converted_sale)
        commission_uah_invoice.reload()
        commission_uah.update(
            {
                "payment_entries": [commission_uah_payment.name],
                "purchase_outstanding_after_payment": float(commission_uah_invoice.outstanding_amount or 0),
                "sale": converted_sale.name,
                "sale_stock_ledger": _ledger_evidence(frappe, [converted_sale.name]),
                "sale_gl": _gl_evidence(frappe, converted_sale.name),
                "own_balance_after_sale": _dimension_balance(
                    frappe,
                    ITEM_CODE,
                    warehouses["OWN"],
                    lots["commission_uah_target"],
                ),
            }
        )
        result["commission_uah"] = commission_uah

        commission_usd_invoice, commission_usd = _execute_conversion(
            frappe,
            company=company,
            supplier=suppliers["USD"],
            payable_account=accounts["supplier_payable_usd"],
            source_warehouse=warehouses["COMMISSION"],
            own_warehouse=warehouses["OWN"],
            relationship_model="COMMISSION",
            source_lot=lots["commission_usd_source"],
            target_lot=lots["commission_usd_target"],
            available_qty=Decimal("2"),
            convert_qty=Decimal("2"),
            return_qty=Decimal("0"),
            unit_cost=Decimal("10"),
            currency="USD",
            exchange_rate=Decimal("40"),
            posting_date=posting_date,
            event_id=f"TP-G0F-{run_id}-COM-USD",
            submitted=submitted,
        )
        usd_payments = []
        for sequence, bank_amount in enumerate([Decimal("410"), Decimal("420")], start=1):
            payment = _make_purchase_payment(
                invoice=commission_usd_invoice,
                obligation_amount=Decimal("10"),
                bank_amount=bank_amount,
                posting_date=posting_date,
                sequence=sequence,
                event_id=commission_usd["event_id"],
            )
            submitted.append(payment)
            usd_payments.append(payment)
        commission_usd_invoice.reload()
        usd_payment_gl = {
            payment.name: _active_gl(frappe, "Payment Entry", payment.name) for payment in usd_payments
        }
        usd_exchange_journals = _exchange_journal_evidence(
            frappe,
            [payment.name for payment in usd_payments],
        )
        commission_usd.update(
            {
                "payment_entries": [payment.name for payment in usd_payments],
                "payment_amounts": [
                    {
                        "paid_amount": float(payment.paid_amount or 0),
                        "received_amount": float(payment.received_amount or 0),
                    }
                    for payment in usd_payments
                ],
                "purchase_outstanding_after_payments": float(commission_usd_invoice.outstanding_amount or 0),
                "payment_gl": usd_payment_gl,
                "exchange_journals": usd_exchange_journals,
                "net_exchange_differences": [
                    _net_exchange_difference(
                        payment.name,
                        payment_gl=usd_payment_gl[payment.name],
                        exchange_journals=usd_exchange_journals,
                        exchange_account=accounts["exchange_gain_loss"],
                    )
                    for payment in usd_payments
                ],
            }
        )
        result["commission_usd"] = commission_usd

        consignment_invoice, consignment = _execute_conversion(
            frappe,
            company=company,
            supplier=suppliers["UAH"],
            payable_account=accounts["supplier_payable"],
            source_warehouse=warehouses["CONSIGNMENT"],
            own_warehouse=warehouses["OWN"],
            relationship_model="CONSIGNMENT",
            source_lot=lots["consignment_uah_source"],
            target_lot=lots["consignment_uah_target"],
            available_qty=Decimal("1"),
            convert_qty=Decimal("1"),
            return_qty=Decimal("0"),
            unit_cost=Decimal("70"),
            currency="UAH",
            exchange_rate=Decimal("1"),
            posting_date=posting_date,
            event_id=f"TP-G0F-{run_id}-CON-UAH",
            submitted=submitted,
        )
        consignment_payment = _make_purchase_payment(
            invoice=consignment_invoice,
            obligation_amount=Decimal("70"),
            bank_amount=Decimal("70"),
            posting_date=posting_date,
            sequence=1,
            event_id=consignment["event_id"],
        )
        submitted.append(consignment_payment)
        consignment_invoice.reload()
        consignment.update(
            {
                "payment_entries": [consignment_payment.name],
                "purchase_outstanding_after_payment": float(consignment_invoice.outstanding_amount or 0),
            }
        )
        result["consignment_uah"] = consignment

        mixed_lot_sale = _make_converted_sale(
            frappe,
            company=company,
            customer=_ensure_customer(frappe),
            warehouse=warehouses["OWN"],
            lot_id=lots["commission_usd_target"],
            qty=Decimal("1"),
            rate=Decimal("500"),
            posting_date=posting_date,
            event_id=commission_usd["event_id"],
        )
        submitted.append(mixed_lot_sale)
        mixed_sale_ledger = _ledger_evidence(frappe, [mixed_lot_sale.name])
        total_purchase_value = sum(
            float(row["stock_value_difference"] or 0)
            for scenario in [commission_uah, commission_usd, consignment]
            for row in scenario["stock_ledger"]
            if row["voucher_type"] == "Purchase Invoice"
        )
        total_cogs = -sum(
            float(row["stock_value_difference"] or 0)
            for row in [*commission_uah["sale_stock_ledger"], *mixed_sale_ledger]
        )
        result["mixed_own_valuation"] = {
            "sale": mixed_lot_sale.name,
            "selected_ownership_lot": lots["commission_usd_target"],
            "selected_lot_purchase_unit_cost_uah": 400.0,
            "stock_ledger": mixed_sale_ledger,
            "actual_fifo_cogs": -sum(float(row["stock_value_difference"] or 0) for row in mixed_sale_ledger),
            "total_purchase_stock_value": total_purchase_value,
            "total_cogs": total_cogs,
            "remaining_aggregate_stock_value": total_purchase_value - total_cogs,
            "remaining_dimension_balances": {
                "commission_uah_own": _dimension_balance(
                    frappe,
                    ITEM_CODE,
                    warehouses["OWN"],
                    lots["commission_uah_target"],
                ),
                "commission_usd_own": _dimension_balance(
                    frappe,
                    ITEM_CODE,
                    warehouses["OWN"],
                    lots["commission_usd_target"],
                ),
                "consignment_uah_own": _dimension_balance(
                    frappe,
                    ITEM_CODE,
                    warehouses["OWN"],
                    lots["consignment_uah_target"],
                ),
            },
            "valuation_scope": "WAREHOUSE_FIFO_NOT_OWNERSHIP_DIMENSION",
        }

        result["serialized_return"] = _run_serialized_return(
            frappe,
            company=company,
            warehouse=warehouses["COMMISSION"],
            source_lot=lots["serialized_source"],
            posting_date=posting_date,
            event_id=f"TP-G0F-{run_id}-SER",
            submitted=submitted,
        )
    finally:
        try:
            result["cancelled_documents"], result["cleanup_errors"] = _cancel_submitted(submitted)
            result["balances_after_cleanup"] = {
                lot_key: _dimension_balance(
                    frappe,
                    SERIAL_ITEM_CODE if lot_key == "serialized_source" else ITEM_CODE,
                    (
                        warehouses["COMMISSION"]
                        if lot_key in {"commission_uah_source", "commission_usd_source", "serialized_source"}
                        else warehouses["CONSIGNMENT"]
                        if lot_key == "consignment_uah_source"
                        else warehouses["OWN"]
                    ),
                    lot_id,
                )
                for lot_key, lot_id in lots.items()
            }
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

    uah = result["commission_uah"]
    if uah["source_balance"] != 0 or uah["own_balance_after_sale"] != 1:
        raise AssertionError(f"Commission UAH quantities did not reconcile: {result}")
    if uah["purchase_outstanding_after_payment"] != 0:
        raise AssertionError(f"Commission UAH payable did not clear: {result}")
    uah_purchase_value = sum(
        float(row["stock_value_difference"] or 0)
        for row in uah["stock_ledger"]
        if row["voucher_type"] == "Purchase Invoice"
    )
    uah_cogs = -sum(float(row["stock_value_difference"] or 0) for row in uah["sale_stock_ledger"])
    if (uah_purchase_value, uah_cogs, uah_purchase_value - uah_cogs) != (160.0, 80.0, 80.0):
        raise AssertionError(f"Converted stock asset and COGS did not reconcile: {result}")

    usd = result["commission_usd"]
    if usd["purchase_outstanding_after_payments"] != 0:
        raise AssertionError(f"Commission USD payable did not clear after two payments: {result}")
    if [row["received_amount"] for row in usd["payment_amounts"]] != [10.0, 10.0]:
        raise AssertionError(f"Commission USD partial payments are incorrect: {result}")
    if usd["net_exchange_differences"] != [10.0, 20.0]:
        raise AssertionError(f"Commission USD exchange differences are incorrect: {result}")
    if usd["own_balance"] != 2 or usd["source_balance"] != 0:
        raise AssertionError(f"Commission USD stock did not convert exactly: {result}")

    consignment = result["consignment_uah"]
    if consignment["source_balance"] != 0 or consignment["own_balance"] != 1:
        raise AssertionError(f"Consignment UAH stock did not convert exactly: {result}")
    if consignment["purchase_outstanding_after_payment"] != 0:
        raise AssertionError(f"Consignment UAH payable did not clear: {result}")

    mixed = result["mixed_own_valuation"]
    if mixed["actual_fifo_cogs"] != 80 or mixed["remaining_aggregate_stock_value"] != 870:
        raise AssertionError(f"Warehouse-level FIFO after conversion did not reconcile: {result}")
    if mixed["remaining_dimension_balances"] != {
        "commission_uah_own": 1.0,
        "commission_usd_own": 1.0,
        "consignment_uah_own": 1.0,
    }:
        raise AssertionError(f"Mixed own-stock dimension balances are incorrect: {result}")

    serialized = result["serialized_return"]
    if serialized["dimension_balance"] != 1:
        raise AssertionError(f"Serialized partial return quantity is incorrect: {result}")
    returned_serial, remaining_serial = serialized["serial_state"].values()
    if returned_serial.warehouse or remaining_serial.warehouse != warehouses["COMMISSION"]:
        raise AssertionError(f"Serialized return moved the wrong Serial No: {result}")
    if result["cleanup_errors"] or any(result["balances_after_cleanup"].values()):
        raise AssertionError(f"Gate 0F cleanup did not restore zero active balances: {result}")
    if result["restored_bundle_setting"] != original_bundle_setting:
        raise AssertionError(f"Gate 0F did not restore the Serial/Batch setting: {result}")

    return result
