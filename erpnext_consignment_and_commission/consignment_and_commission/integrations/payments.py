"""Controlled Payment Entry lifecycle for one settlement report."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Any

from ..services.payment import (
    SettlementPaymentError,
    SettlementPaymentRequest,
    payment_fingerprint,
    validate_payment_request,
)
from ..setup.ownership_dimension import (
    PAYMENT_FINGERPRINT_FIELD,
    PAYMENT_IDEMPOTENCY_FIELD,
    SETTLEMENT_REPORT_FIELD,
)
from .sale_allocations import get_account_mapping
from .settlements import get_supplier_payable_account, refresh_settlement_lifecycle


def _reference_reports(frappe: Any, doc: Any) -> set[str]:
    journal_names = [
        row.reference_name
        for row in doc.references
        if row.reference_doctype == "Journal Entry" and row.reference_name
    ]
    if not journal_names:
        return set()
    return {
        value
        for value in frappe.get_all(
            "Journal Entry",
            filters={"name": ("in", journal_names)},
            pluck=SETTLEMENT_REPORT_FIELD,
        )
        if value
    }


def _request_from_payment(doc: Any) -> SettlementPaymentRequest:
    from frappe.utils import getdate

    return SettlementPaymentRequest(
        idempotency_key=doc.get(PAYMENT_IDEMPOTENCY_FIELD) or "",
        settlement_report=doc.get(SETTLEMENT_REPORT_FIELD) or "",
        bank_account=doc.paid_from or "",
        amount=Decimal(str(doc.received_amount or 0)),
        posting_date=getdate(doc.posting_date),
        reference_no=doc.reference_no or "",
        exchange_rate=Decimal(str(doc.target_exchange_rate or 0)),
    )


def validate_settlement_payment(doc: Any, method: str | None = None) -> None:
    """Fail closed when any Payment Entry touches a CC settlement debt JE."""
    del method
    import frappe

    reference_reports = _reference_reports(frappe, doc)
    linked_report = doc.get(SETTLEMENT_REPORT_FIELD)
    if not reference_reports and not linked_report:
        return
    if not linked_report or reference_reports != {linked_report}:
        frappe.throw("Payment Entry must reference debt from exactly its CC Settlement Report")
    report = frappe.get_doc("CC Settlement Report", linked_report)
    if report.docstatus != 1 or report.status not in {"PAYABLE", "PARTIALLY_PAID", "PAID"}:
        frappe.throw("CC Settlement Report must be submitted and payable")
    if doc.payment_type != "Pay" or doc.party_type != "Supplier" or doc.party != report.supplier:
        frappe.throw("Settlement Payment Entry must pay the report Supplier")
    mapping = get_account_mapping(frappe, report.company)
    try:
        payable = get_supplier_payable_account(
            frappe,
            company=report.company,
            supplier=report.supplier,
            currency=report.currency,
            fallback=mapping.default_supplier_payable_account,
        )
    except SettlementPaymentError as exc:
        frappe.throw(str(exc))
    except ValueError as exc:
        frappe.throw(str(exc))
    if doc.company != report.company or doc.paid_to != payable:
        frappe.throw("Settlement Payment Entry uses an unexpected Company or payable account")
    references = [
        row
        for row in doc.references
        if row.reference_doctype == "Journal Entry" and row.reference_name
    ]
    if len(references) != 1 or references[0].reference_name != report.debt_journal_entry:
        frappe.throw("Settlement Payment Entry must reference exactly the report debt Journal Entry")
    request = _request_from_payment(doc)
    try:
        validate_payment_request(request)
    except SettlementPaymentError as exc:
        frappe.throw(str(exc))
    if doc.get(PAYMENT_FINGERPRINT_FIELD) != payment_fingerprint(request):
        frappe.throw("Settlement Payment Entry differs from its immutable request fingerprint")
    company_currency = frappe.get_cached_value("Company", report.company, "default_currency")
    if Decimal(str(doc.source_exchange_rate or 0)) != 1:
        frappe.throw("Settlement payment source account must use Company currency rate 1")
    if report.currency == company_currency and request.exchange_rate != 1:
        frappe.throw("Company-currency settlement payment requires exchange rate 1")
    amount = Decimal(str(doc.received_amount or 0))
    allocated = Decimal(str(references[0].allocated_amount or 0))
    quantum = Decimal("1").scaleb(-int(doc.precision("paid_amount") or 2))
    expected_base = (amount * request.exchange_rate).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )
    if amount != allocated or Decimal(str(doc.paid_amount or 0)) != expected_base:
        frappe.throw("Settlement payment and allocated obligation amounts must match")
    if doc.docstatus == 0 and amount > Decimal(str(report.outstanding_amount or 0)):
        frappe.throw("Settlement payment exceeds the report outstanding amount")


def _existing_payment(frappe: Any, request: SettlementPaymentRequest, fingerprint: str) -> Any | None:
    name = frappe.db.get_value(
        "Payment Entry",
        {PAYMENT_IDEMPOTENCY_FIELD: request.idempotency_key},
        "name",
    )
    if not name:
        return None
    payment = frappe.get_doc("Payment Entry", name)
    if payment.get(PAYMENT_FINGERPRINT_FIELD) != fingerprint:
        raise SettlementPaymentError(
            f"Payment idempotency key {request.idempotency_key!r} belongs to another request"
        )
    return payment


def create_settlement_payment(request: SettlementPaymentRequest) -> Any:
    """Create an idempotent draft Payment Entry for one report balance."""
    import frappe

    if frappe.db.transaction_writes:
        raise SettlementPaymentError(
            "Settlement Payment creation must start before unrelated transaction writes"
        )
    validate_payment_request(request)
    fingerprint = payment_fingerprint(request)
    existing = _existing_payment(frappe, request, fingerprint)
    if existing:
        return existing
    rows = frappe.db.sql(
        """
        select name, docstatus, status, outstanding_amount
        from `tabCC Settlement Report`
        where name = %s
        for update
        """,
        (request.settlement_report,),
        as_dict=True,
    )
    if not rows or rows[0].docstatus != 1 or rows[0].status not in {
        "PAYABLE",
        "PARTIALLY_PAID",
    }:
        raise SettlementPaymentError("Settlement Report is not payable")
    if request.amount > Decimal(str(rows[0].outstanding_amount or 0)):
        raise SettlementPaymentError("Payment exceeds the report outstanding amount")

    report = frappe.get_doc("CC Settlement Report", request.settlement_report)
    mapping = get_account_mapping(frappe, report.company)
    company = frappe.get_cached_doc("Company", report.company)
    if report.currency == company.default_currency and request.exchange_rate != 1:
        raise SettlementPaymentError("Company-currency settlement payment requires rate 1")
    try:
        payable = get_supplier_payable_account(
            frappe,
            company=report.company,
            supplier=report.supplier,
            currency=report.currency,
            fallback=mapping.default_supplier_payable_account,
        )
    except ValueError as exc:
        raise SettlementPaymentError(str(exc)) from exc
    bank = frappe.db.get_value(
        "Account",
        request.bank_account,
        ["company", "root_type", "is_group", "account_currency"],
        as_dict=True,
    )
    if (
        not bank
        or bank.company != report.company
        or bank.root_type != "Asset"
        or bank.is_group
        or bank.account_currency != company.default_currency
    ):
        raise SettlementPaymentError("Payment bank/cash account is incompatible with the report")
    debt = frappe.get_doc("Journal Entry", report.debt_journal_entry)
    if debt.docstatus != 1:
        raise SettlementPaymentError("Settlement debt Journal Entry is not submitted")

    amount = Decimal(str(request.amount))
    currency_precision = int(
        frappe.db.get_single_value("System Settings", "currency_precision") or 2
    )
    quantum = Decimal("1").scaleb(-currency_precision)
    base_amount = (amount * Decimal(str(request.exchange_rate))).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )
    payment = frappe.get_doc(
        {
            "doctype": "Payment Entry",
            "payment_type": "Pay",
            "company": report.company,
            "posting_date": request.posting_date,
            "party_type": "Supplier",
            "party": report.supplier,
            "paid_from": request.bank_account,
            "paid_to": payable,
            "paid_from_account_currency": company.default_currency,
            "paid_to_account_currency": report.currency,
            "paid_amount": float(base_amount),
            "received_amount": float(amount),
            "source_exchange_rate": 1,
            "target_exchange_rate": float(request.exchange_rate),
            "reference_no": request.reference_no,
            "reference_date": request.posting_date,
            "cost_center": company.cost_center,
            SETTLEMENT_REPORT_FIELD: report.name,
            PAYMENT_IDEMPOTENCY_FIELD: request.idempotency_key,
            PAYMENT_FINGERPRINT_FIELD: fingerprint,
            "references": [
                {
                    "reference_doctype": "Journal Entry",
                    "reference_name": debt.name,
                    "total_amount": float(report.total_partner_amount),
                    "outstanding_amount": float(report.outstanding_amount),
                    "allocated_amount": float(amount),
                }
            ],
        }
    )
    name = "CC-PAY-" + sha256(
        f"{request.idempotency_key}:{fingerprint}".encode()
    ).hexdigest()[:20].upper()
    payment.insert(ignore_permissions=True, set_name=name)
    return payment


def submit_settlement_payment(name: str) -> Any:
    import frappe

    payment = frappe.get_doc("Payment Entry", name)
    if payment.docstatus == 2:
        raise SettlementPaymentError("Cancelled settlement Payment Entry cannot be submitted")
    if payment.docstatus == 0:
        payment.submit()
    return payment


def update_settlement_after_payment(doc: Any, method: str | None = None) -> None:
    del method
    import frappe

    report_name = doc.get(SETTLEMENT_REPORT_FIELD)
    if not report_name:
        return
    if not frappe.db.exists("CC Settlement Report", report_name):
        frappe.throw("Linked CC Settlement Report does not exist")
    refresh_settlement_lifecycle(frappe, report_name)
