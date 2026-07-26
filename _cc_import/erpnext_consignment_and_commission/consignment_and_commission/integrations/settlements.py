"""Controlled settlement-report and Supplier debt lifecycle."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Any

from ..doctype.cc_settlement_report.cc_settlement_report import WRITE_FLAG
from ..services.settlement import (
    SettlementError,
    SettlementRequest,
    calculate_reportable_partner_balance,
    settlement_fingerprint,
    validate_settlement_request,
)
from ..setup.ownership_dimension import POSTING_KIND_FIELD, SETTLEMENT_REPORT_FIELD
from .sale_allocations import get_account_mapping

SETTLEMENT_CANCELLATION_FLAG = "cc_settlement_cancellation"


@contextmanager
def _settlement_write(frappe: Any):
    previous = getattr(frappe.flags, WRITE_FLAG, False)
    setattr(frappe.flags, WRITE_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, WRITE_FLAG, previous)


def _existing_report(frappe: Any, request: SettlementRequest, fingerprint: str) -> Any | None:
    name = frappe.db.get_value(
        "CC Settlement Report",
        {"idempotency_key": request.idempotency_key},
        "name",
    )
    if not name:
        return None
    report = frappe.get_doc("CC Settlement Report", name)
    if report.request_fingerprint != fingerprint:
        raise SettlementError(
            f"Settlement idempotency key {request.idempotency_key!r} belongs to another request"
        )
    return report


def _locked_sale_allocations(frappe: Any, names: tuple[str, ...]) -> list[Any]:
    placeholders = ", ".join(["%s"] * len(names))
    locked = frappe.db.sql(
        f"""
        select name
        from `tabCC Sale Allocation`
        where name in ({placeholders})
        order by name
        for update
        """,
        tuple(sorted(names)),
        as_dict=True,
    )
    if {row.name for row in locked} != set(names):
        raise SettlementError("One or more CC Sale Allocations do not exist")
    return [frappe.get_doc("CC Sale Allocation", name) for name in names]


def get_supplier_payable_account(
    frappe: Any,
    *,
    company: str,
    supplier: str,
    currency: str,
    fallback: str,
) -> str:
    """Resolve the Supplier-specific payable and prove its account currency."""
    from erpnext.accounts.party import get_party_account

    payable = get_party_account("Supplier", supplier, company) or fallback
    metadata = frappe.db.get_value(
        "Account",
        payable,
        ["company", "is_group", "account_type", "account_currency"],
        as_dict=True,
    )
    if (
        not metadata
        or metadata.company != company
        or metadata.is_group
        or metadata.account_type != "Payable"
        or metadata.account_currency != currency
    ):
        raise SettlementError(
            f"Supplier {supplier} requires a {currency} Payable account for Company {company}"
        )
    return payable


def create_settlement_report(request: SettlementRequest) -> Any:
    """Create an idempotent draft report and exclusively bind its sold slices."""
    import frappe
    from frappe.utils import getdate

    if frappe.db.transaction_writes:
        raise SettlementError(
            "Settlement Report creation must start before unrelated transaction writes"
        )
    validate_settlement_request(request)
    fingerprint = settlement_fingerprint(request)
    existing = _existing_report(frappe, request, fingerprint)
    if existing:
        return existing
    settings = frappe.get_single("CC Settings")
    if not settings.enabled:
        raise SettlementError("CC Settings must be enabled before a settlement can be created")

    allocations = _locked_sale_allocations(frappe, request.sale_allocations)
    first = allocations[0]
    coordinates = {
        "company": first.company,
        "partner_profile": first.partner_profile,
        "supplier": first.supplier,
        "contract": first.contract,
        "relationship_model": first.relationship_model,
        "currency": first.currency,
    }
    if first.relationship_model not in {"COMMISSION", "CONSIGNMENT"}:
        raise SettlementError("OWN sale allocations do not create a partner settlement")
    rows = []
    total = Decimal("0")
    base_total = Decimal("0")
    for allocation in allocations:
        mismatches = [
            fieldname
            for fieldname, value in coordinates.items()
            if str(allocation.get(fieldname) or "") != str(value or "")
        ]
        if mismatches:
            raise SettlementError(
                "One Settlement Report cannot mix partner coordinates: "
                + ", ".join(mismatches)
            )
        if allocation.status not in {"SOLD", "PARTIALLY_RETURNED"}:
            raise SettlementError(f"CC Sale Allocation {allocation.name} is not reportable")
        if allocation.settlement_report:
            raise SettlementError(
                f"CC Sale Allocation {allocation.name} already belongs to "
                f"{allocation.settlement_report}"
            )
        sale_date = getdate(allocation.posting_date)
        if sale_date < request.period_from or sale_date > request.period_to:
            raise SettlementError(
                f"CC Sale Allocation {allocation.name} is outside the settlement period"
            )
        return_audits = frappe.get_all(
            "CC Sale Return Allocation",
            filters={"sale_allocation": allocation.name, "status": "RETURNED"},
            fields=[
                "returned_qty",
                "partner_amount",
                "base_partner_amount",
                "currency",
                "relationship_model",
            ],
        )
        audited_returned_qty = sum(
            (Decimal(str(row.returned_qty or 0)) for row in return_audits),
            Decimal("0"),
        )
        if audited_returned_qty != Decimal(str(allocation.returned_qty or 0)):
            raise SettlementError(
                f"CC Sale Allocation {allocation.name} return quantity does not reconcile"
            )
        if any(
            row.currency != allocation.currency
            or row.relationship_model != allocation.relationship_model
            for row in return_audits
        ):
            raise SettlementError(
                f"CC Sale Allocation {allocation.name} return financial audit is inconsistent"
            )
        reversed_partner = sum(
            (Decimal(str(row.partner_amount or 0)) for row in return_audits),
            Decimal("0"),
        )
        reportable = calculate_reportable_partner_balance(
            partner_amount=allocation.partner_amount,
            reversed_partner_amount=reversed_partner,
        )
        reversed_base_partner = sum(
            (Decimal(str(row.base_partner_amount or 0)) for row in return_audits),
            Decimal("0"),
        )
        base_reportable = calculate_reportable_partner_balance(
            partner_amount=allocation.base_partner_amount,
            reversed_partner_amount=reversed_base_partner,
        )
        if reportable <= 0:
            raise SettlementError(
                f"CC Sale Allocation {allocation.name} has no reportable partner amount"
            )
        if base_reportable <= 0:
            raise SettlementError(
                f"CC Sale Allocation {allocation.name} has no reportable base partner amount"
            )
        rows.append((allocation, reportable, base_reportable))
        total += reportable
        base_total += base_reportable

    contract = frappe.get_doc("CC Contract", coordinates["contract"])
    due_date = request.posting_date + timedelta(days=int(contract.settlement_deadline_days or 0))
    company_currency = frappe.get_cached_value("Company", coordinates["company"], "default_currency")
    if coordinates["currency"] == company_currency:
        if total != base_total:
            raise SettlementError("Company-currency settlement amount differs from its base amount")
        conversion_rate = Decimal("1")
    else:
        conversion_rate = (base_total / total).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_HALF_UP,
        )
    name = "CC-SET-" + sha256(
        f"{request.idempotency_key}:{fingerprint}".encode()
    ).hexdigest()[:20].upper()
    report = frappe.get_doc(
        {
            "doctype": "CC Settlement Report",
            "status": "DRAFT",
            "idempotency_key": request.idempotency_key,
            "request_fingerprint": fingerprint,
            **coordinates,
            "conversion_rate": conversion_rate,
            "period_from": request.period_from,
            "period_to": request.period_to,
            "posting_date": request.posting_date,
            "due_date": due_date,
            "total_partner_amount": total,
            "base_total_partner_amount": base_total,
            "adjusted_amount": 0,
            "base_adjusted_amount": 0,
            "net_partner_amount": total,
            "paid_amount": 0,
            "outstanding_amount": total,
            "partner_credit_amount": 0,
        }
    )
    for allocation, reportable, base_reportable in rows:
        report.append(
            "items",
            {
                "sale_allocation": allocation.name,
                "sales_invoice": allocation.sales_invoice,
                "posting_date": allocation.posting_date,
                "item_code": allocation.item_code,
                "stock_lot": allocation.stock_lot,
                "source_method": allocation.source_method,
                "sold_qty": allocation.sold_qty,
                "returned_qty": allocation.returned_qty,
                "net_amount": allocation.net_amount,
                "partner_amount": reportable,
                "base_partner_amount": base_reportable,
            },
        )
    with _settlement_write(frappe):
        report.insert(ignore_permissions=True, set_name=name)
    for allocation, _reportable, _base_reportable in rows:
        frappe.db.set_value(
            "CC Sale Allocation",
            allocation.name,
            "settlement_report",
            report.name,
            update_modified=False,
        )
    return report


def post_settlement_debt(report: Any) -> None:
    """Move unreported partner liability to a named Supplier payable."""
    import frappe

    settings = frappe.get_single("CC Settings")
    if not settings.enabled:
        frappe.throw("CC Settings must be enabled before Settlement Report submit")
    mapping = get_account_mapping(frappe, report.company)
    company = frappe.get_cached_doc("Company", report.company)
    conversion_rate = Decimal(str(report.conversion_rate or 0))
    amount = Decimal(str(report.total_partner_amount or 0))
    base_amount = Decimal(str(report.base_total_partner_amount or 0))
    if conversion_rate <= 0 or amount <= 0 or base_amount <= 0:
        frappe.throw("Settlement currency amounts and conversion rate must be positive")
    if report.currency == company.default_currency and (
        conversion_rate != 1 or amount != base_amount
    ):
        frappe.throw("Company-currency settlement requires equal base amount and rate 1")
    source_account = {
        "COMMISSION": mapping.unreported_commission_liability_account,
        "CONSIGNMENT": mapping.unreported_consignment_liability_account,
    }[report.relationship_model]
    try:
        payable = get_supplier_payable_account(
            frappe,
            company=report.company,
            supplier=report.supplier,
            currency=report.currency,
            fallback=mapping.default_supplier_payable_account,
        )
    except SettlementError as exc:
        frappe.throw(str(exc))

    existing = frappe.db.get_value(
        "Journal Entry",
        {SETTLEMENT_REPORT_FIELD: report.name, POSTING_KIND_FIELD: "SETTLEMENT_DEBT"},
        "name",
    )
    if existing:
        journal = frappe.get_doc("Journal Entry", existing)
        if journal.docstatus != 1:
            frappe.throw(f"Settlement debt Journal Entry {journal.name} is not submitted")
    else:
        journal = frappe.get_doc(
            {
                "doctype": "Journal Entry",
                "company": report.company,
                "posting_date": report.posting_date,
                "voucher_type": "Journal Entry",
                "multi_currency": int(report.currency != company.default_currency),
                "user_remark": f"CC settlement debt for {report.name}",
                SETTLEMENT_REPORT_FIELD: report.name,
                POSTING_KIND_FIELD: "SETTLEMENT_DEBT",
            }
        )
        journal.append(
            "accounts",
            {
                "account": source_account,
                "debit_in_account_currency": base_amount,
                "credit_in_account_currency": 0,
                "exchange_rate": 1,
                "cost_center": company.cost_center,
            },
        )
        journal.append(
            "accounts",
            {
                "account": payable,
                "party_type": "Supplier",
                "party": report.supplier,
                "debit_in_account_currency": 0,
                "credit_in_account_currency": amount,
                "exchange_rate": float(conversion_rate),
                "cost_center": company.cost_center,
            },
        )
        name = "CC-DEBT-" + sha256(report.name.encode()).hexdigest()[:20].upper()
        journal.insert(ignore_permissions=True, set_name=name)
        journal.submit()

    report.debt_journal_entry = journal.name
    report.status = "PAYABLE"
    for row in report.items:
        frappe.db.set_value(
            "CC Sale Allocation",
            row.sale_allocation,
            {
                "status": "REPORTED",
                "settled_partner_amount": row.partner_amount,
            },
            update_modified=False,
        )


@contextmanager
def _settlement_cancellation(frappe: Any):
    previous = getattr(frappe.flags, SETTLEMENT_CANCELLATION_FLAG, False)
    setattr(frappe.flags, SETTLEMENT_CANCELLATION_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, SETTLEMENT_CANCELLATION_FLAG, previous)


def cancel_settlement_debt(report: Any) -> None:
    import frappe

    if Decimal(str(report.paid_amount or 0)) != 0:
        frappe.throw("Cancel linked payments before cancelling this Settlement Report")
    if frappe.db.exists(
        "CC Settlement Adjustment",
        {"settlement_report": report.name, "docstatus": 1},
    ):
        frappe.throw("Cancel linked Settlement Adjustments before cancelling this report")
    if report.debt_journal_entry:
        journal = frappe.get_doc("Journal Entry", report.debt_journal_entry)
        if journal.docstatus == 1:
            ignored = set(journal.get("ignore_linked_doctypes") or ())
            ignored.add("CC Settlement Report")
            journal.ignore_linked_doctypes = tuple(sorted(ignored))
            with _settlement_cancellation(frappe):
                journal.cancel()
    report.status = "CANCELLED"


def release_report_allocations(report: Any, *, cancelled: bool) -> None:
    import frappe

    for row in report.items:
        sale = frappe.db.get_value(
            "CC Sale Allocation",
            row.sale_allocation,
            ["returned_qty", "settlement_report"],
            as_dict=True,
        )
        if not sale or sale.settlement_report != report.name:
            continue
        status = "PARTIALLY_RETURNED" if Decimal(str(sale.returned_qty or 0)) else "SOLD"
        frappe.db.set_value(
            "CC Sale Allocation",
            row.sale_allocation,
            {
                "status": status,
                "settled_partner_amount": 0,
                "settlement_report": None,
            },
            update_modified=False,
        )
    if cancelled:
        report.status = "CANCELLED"


def refresh_settlement_lifecycle(frappe: Any, report_name: str) -> Any:
    """Recalculate debt, payment, return-adjustment and partner-credit state under lock."""
    frappe.db.sql(
        "select name from `tabCC Settlement Report` where name = %s for update",
        (report_name,),
    )
    report = frappe.get_doc("CC Settlement Report", report_name)
    if report.docstatus != 1:
        return report
    paid = sum(
        (
            Decimal(str(value or 0))
            for value in frappe.get_all(
                "Payment Entry",
                filters={SETTLEMENT_REPORT_FIELD: report_name, "docstatus": 1},
                pluck="received_amount",
            )
        ),
        Decimal("0"),
    )
    adjustment_rows = frappe.get_all(
        "CC Settlement Adjustment",
        filters={"settlement_report": report_name, "docstatus": 1},
        fields=["amount", "base_amount"],
    )
    adjusted = sum(
        (Decimal(str(row.amount or 0)) for row in adjustment_rows),
        Decimal("0"),
    )
    base_adjusted = sum(
        (Decimal(str(row.base_amount or 0)) for row in adjustment_rows),
        Decimal("0"),
    )
    total = Decimal(str(report.total_partner_amount or 0))
    base_total = Decimal(str(report.base_total_partner_amount or 0))
    if adjusted < 0 or adjusted > total or base_adjusted < 0 or base_adjusted > base_total:
        frappe.throw("Settlement adjustments exceed the immutable report balance")
    net = total - adjusted
    outstanding = max(net - paid, Decimal("0"))
    partner_credit = max(paid - net, Decimal("0"))
    if partner_credit:
        status = "CREDIT_DUE"
    elif net == 0:
        status = "ADJUSTED"
    elif outstanding == 0:
        status = "PAID"
    elif paid:
        status = "PARTIALLY_PAID"
    else:
        status = "PAYABLE"
    frappe.db.set_value(
        "CC Settlement Report",
        report.name,
        {
            "status": status,
            "adjusted_amount": adjusted,
            "base_adjusted_amount": base_adjusted,
            "net_partner_amount": net,
            "paid_amount": paid,
            "outstanding_amount": outstanding,
            "partner_credit_amount": partner_credit,
        },
        update_modified=False,
    )
    report.reload()
    return report


def submit_settlement_report(name: str) -> Any:
    import frappe

    report = frappe.get_doc("CC Settlement Report", name)
    if report.docstatus == 2:
        raise SettlementError("Cancelled Settlement Report cannot be submitted")
    if report.docstatus == 0:
        with _settlement_write(frappe):
            report.submit()
    return report


def cancel_settlement_report(name: str) -> Any:
    import frappe

    report = frappe.get_doc("CC Settlement Report", name)
    if report.docstatus == 1:
        with _settlement_write(frappe):
            report.cancel()
    return report


def guard_settlement_debt_cancellation(doc: Any, method: str | None = None) -> None:
    del method
    import frappe

    if doc.get(SETTLEMENT_REPORT_FIELD) and not getattr(
        frappe.flags,
        SETTLEMENT_CANCELLATION_FLAG,
        False,
    ):
        frappe.throw(
            f"Cancel CC Settlement Report {doc.get(SETTLEMENT_REPORT_FIELD)} "
            "instead of its debt Journal Entry"
        )
