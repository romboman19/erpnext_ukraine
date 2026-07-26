"""Immutable settlement corrections created by reported managed returns."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Any

from ..doctype.cc_settlement_adjustment.cc_settlement_adjustment import WRITE_FLAG
from ..setup.ownership_dimension import POSTING_KIND_FIELD, SETTLEMENT_ADJUSTMENT_FIELD
from .sale_allocations import get_account_mapping
from .settlements import get_supplier_payable_account

ADJUSTMENT_CANCELLATION_FLAG = "cc_settlement_adjustment_cancellation"


@contextmanager
def _adjustment_write(frappe: Any):
    previous = getattr(frappe.flags, WRITE_FLAG, False)
    setattr(frappe.flags, WRITE_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, WRITE_FLAG, previous)


@contextmanager
def _adjustment_cancellation(frappe: Any):
    previous = getattr(frappe.flags, ADJUSTMENT_CANCELLATION_FLAG, False)
    setattr(frappe.flags, ADJUSTMENT_CANCELLATION_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, ADJUSTMENT_CANCELLATION_FLAG, previous)


def _currency_quantum(frappe: Any) -> Decimal:
    precision = int(frappe.db.get_single_value("System Settings", "currency_precision") or 2)
    return Decimal("1").scaleb(-precision)


def create_return_settlement_adjustments(doc: Any, audits: list[Any]) -> list[Any]:
    """Create and submit one exact correction per affected historical report."""
    import frappe

    grouped: dict[str, list[Any]] = {}
    for audit in audits:
        if audit.settlement_report:
            grouped.setdefault(audit.settlement_report, []).append(audit)
    adjustments = []
    for report_name in sorted(grouped):
        frappe.db.sql(
            "select name from `tabCC Settlement Report` where name = %s for update",
            (report_name,),
        )
        report = frappe.get_doc("CC Settlement Report", report_name)
        if report.docstatus != 1 or report.status == "CANCELLED":
            frappe.throw(f"CC Settlement Report {report.name} is not adjustable")
        report_audits = grouped[report_name]
        amount = sum(
            (Decimal(str(row.partner_amount or 0)) for row in report_audits),
            Decimal("0"),
        )
        base_amount = sum(
            (Decimal(str(row.base_partner_amount or 0)) for row in report_audits),
            Decimal("0"),
        )
        if amount <= 0 or base_amount <= 0:
            frappe.throw("Reported return must reverse a positive partner obligation")
        current_adjusted = Decimal(str(report.adjusted_amount or 0))
        if current_adjusted + amount > Decimal(str(report.total_partner_amount or 0)):
            frappe.throw(f"Settlement adjustment exceeds {report.name} partner amount")
        outstanding = Decimal(str(report.outstanding_amount or 0))
        applied = min(amount, outstanding)
        credit_due = amount - applied
        conversion_rate = (base_amount / amount).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_HALF_UP,
        )
        existing_name = frappe.db.get_value(
            "CC Settlement Adjustment",
            {
                "settlement_report": report.name,
                "return_sales_invoice": doc.name,
                "docstatus": ("<", 2),
            },
            "name",
        )
        if existing_name:
            adjustment = frappe.get_doc("CC Settlement Adjustment", existing_name)
        else:
            adjustment = frappe.get_doc(
                {
                    "doctype": "CC Settlement Adjustment",
                    "status": "DRAFT",
                    "adjustment_type": "RETURN_REVERSAL",
                    "settlement_report": report.name,
                    "return_sales_invoice": doc.name,
                    "posting_date": doc.posting_date,
                    "company": report.company,
                    "supplier": report.supplier,
                    "relationship_model": report.relationship_model,
                    "currency": report.currency,
                    "amount": amount,
                    "base_amount": base_amount,
                    "conversion_rate": conversion_rate,
                    "applied_to_outstanding_amount": applied,
                    "credit_due_amount": credit_due,
                }
            )
            name = "CC-ADJ-" + sha256(
                f"{report.name}:{doc.name}".encode()
            ).hexdigest()[:20].upper()
            with _adjustment_write(frappe):
                adjustment.insert(ignore_permissions=True, set_name=name)
        if adjustment.docstatus == 0:
            adjustment.flags.ignore_permissions = True
            with _adjustment_write(frappe):
                adjustment.submit()
        adjustments.append(adjustment)
        for audit in report_audits:
            frappe.db.set_value(
                "CC Sale Return Allocation",
                audit.name,
                "settlement_adjustment",
                adjustment.name,
                update_modified=False,
            )
    return adjustments


def post_adjustment_journal(adjustment: Any) -> None:
    """Reduce the referenced payable or create a Supplier credit when already paid."""
    import frappe

    report = frappe.get_doc("CC Settlement Report", adjustment.settlement_report)
    if report.docstatus != 1:
        frappe.throw("Settlement adjustment requires a submitted report")
    mapping = get_account_mapping(frappe, adjustment.company)
    company = frappe.get_cached_doc("Company", adjustment.company)
    payable = get_supplier_payable_account(
        frappe,
        company=adjustment.company,
        supplier=adjustment.supplier,
        currency=adjustment.currency,
        fallback=mapping.default_supplier_payable_account,
    )
    source_account = {
        "COMMISSION": mapping.unreported_commission_liability_account,
        "CONSIGNMENT": mapping.unreported_consignment_liability_account,
    }[adjustment.relationship_model]
    amount = Decimal(str(adjustment.amount))
    base_amount = Decimal(str(adjustment.base_amount))
    applied = Decimal(str(adjustment.applied_to_outstanding_amount or 0))
    credit_due = Decimal(str(adjustment.credit_due_amount or 0))
    quantum = _currency_quantum(frappe)

    journal = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "company": adjustment.company,
            "posting_date": adjustment.posting_date,
            "voucher_type": "Journal Entry",
            "multi_currency": int(adjustment.currency != company.default_currency),
            "user_remark": f"CC settlement return adjustment {adjustment.name}",
            SETTLEMENT_ADJUSTMENT_FIELD: adjustment.name,
            POSTING_KIND_FIELD: "SETTLEMENT_ADJUSTMENT",
        }
    )
    journal.append(
        "accounts",
        {
            "account": source_account,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": base_amount,
            "exchange_rate": 1,
            "cost_center": company.cost_center,
        },
    )
    applied_base = Decimal("0")
    if applied:
        applied_base = (base_amount * applied / amount).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
        journal.append(
            "accounts",
            {
                "account": payable,
                "party_type": "Supplier",
                "party": adjustment.supplier,
                "debit_in_account_currency": applied,
                "credit_in_account_currency": 0,
                "exchange_rate": float(applied_base / applied),
                "reference_type": "Journal Entry",
                "reference_name": report.debt_journal_entry,
                "cost_center": company.cost_center,
            },
        )
    if credit_due:
        credit_base = base_amount - applied_base
        journal.append(
            "accounts",
            {
                "account": payable,
                "party_type": "Supplier",
                "party": adjustment.supplier,
                "debit_in_account_currency": credit_due,
                "credit_in_account_currency": 0,
                "exchange_rate": float(credit_base / credit_due),
                "is_advance": "Yes",
                "cost_center": company.cost_center,
            },
        )
    name = "CC-ADJ-JE-" + sha256(adjustment.name.encode()).hexdigest()[:20].upper()
    journal.insert(ignore_permissions=True, set_name=name)
    journal.submit()
    adjustment.journal_entry = journal.name
    adjustment.status = "POSTED"


def cancel_adjustment_journal(adjustment: Any) -> None:
    import frappe

    if adjustment.journal_entry:
        journal = frappe.get_doc("Journal Entry", adjustment.journal_entry)
        if journal.docstatus == 1:
            ignored = set(journal.get("ignore_linked_doctypes") or ())
            ignored.add("CC Settlement Adjustment")
            journal.ignore_linked_doctypes = tuple(sorted(ignored))
            with _adjustment_cancellation(frappe):
                journal.cancel()
    adjustment.status = "CANCELLED"


def cancel_return_settlement_adjustments(return_sales_invoice: str) -> None:
    import frappe

    names = frappe.get_all(
        "CC Settlement Adjustment",
        filters={"return_sales_invoice": return_sales_invoice, "docstatus": 1},
        pluck="name",
        order_by="name desc",
    )
    for name in names:
        adjustment = frappe.get_doc("CC Settlement Adjustment", name)
        adjustment.flags.ignore_permissions = True
        with _adjustment_write(frappe):
            adjustment.cancel()


def guard_adjustment_journal_cancellation(doc: Any, method: str | None = None) -> None:
    del method
    import frappe

    if doc.get(SETTLEMENT_ADJUSTMENT_FIELD) and not getattr(
        frappe.flags,
        ADJUSTMENT_CANCELLATION_FLAG,
        False,
    ):
        frappe.throw(
            f"Cancel CC Settlement Adjustment {doc.get(SETTLEMENT_ADJUSTMENT_FIELD)} "
            "instead of its Journal Entry"
        )
