"""Immutable sold-slice financial snapshots and recognition postings."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
from typing import Any

from ..doctype.cc_sale_allocation.cc_sale_allocation import WRITE_FLAG
from ..services.sale_financials import (
    SaleFinancialError,
    calculate_sale_financials,
    convert_sale_financials_to_base,
)
from ..setup.ownership_dimension import (
    ALLOCATION_FIELD,
    ALLOCATION_SLICE_FIELD,
    OWNERSHIP_FIELD,
    POSTING_KIND_FIELD,
    RELATIONSHIP_MODEL_FIELD,
    SALES_INVOICE_FIELD,
)
from .pricing import PriceResolutionError, get_effective_price_version

RECOGNITION_CANCELLATION_FLAG = "cc_recognition_cancellation"


@contextmanager
def _sale_allocation_write(frappe: Any):
    previous = getattr(frappe.flags, WRITE_FLAG, False)
    setattr(frappe.flags, WRITE_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, WRITE_FLAG, previous)


def get_account_mapping(frappe: Any, company: str) -> Any:
    if not frappe.db.exists("CC Account Mapping", company):
        raise SaleFinancialError(f"Company {company} requires a CC Account Mapping")
    mapping = frappe.get_doc("CC Account Mapping", company)
    mapping.run_method("validate")
    return mapping


def _posting_datetime(doc: Any) -> Any:
    from frappe.utils import get_datetime

    return get_datetime(f"{doc.posting_date} {doc.posting_time or '00:00:00'}")


def _financial_context(frappe: Any, doc: Any, row: Any) -> Any:
    relationship_model = row.get(RELATIONSHIP_MODEL_FIELD)
    lot = frappe.db.get_value(
        "CC Stock Lot",
        row.get(OWNERSHIP_FIELD),
        [
            "partner_profile",
            "contract",
            "supplier",
            "relationship_model",
            "source_method",
        ],
        as_dict=True,
    )
    if not lot or lot.relationship_model != relationship_model:
        raise SaleFinancialError(f"Sales Invoice row {row.idx} has no matching CC Stock Lot")
    precision = int(row.precision("net_amount") or 2)
    contract = None
    commission_rate: Decimal | None = None
    price_version = None
    partner_rate: Decimal | None = None
    if relationship_model in {"COMMISSION", "CONSIGNMENT"}:
        if not lot.contract or not lot.supplier:
            raise SaleFinancialError(
                f"CC Stock Lot {row.get(OWNERSHIP_FIELD)} has no partner Contract"
            )
        contract = frappe.db.get_value(
            "CC Contract",
            lot.contract,
            [
                "status",
                "valid_from",
                "valid_to",
                "currency",
                "commission_rate",
            ],
            as_dict=True,
        )
        from frappe.utils import getdate

        if (
            not contract
            or contract.status != "ACTIVE"
            or getdate(doc.posting_date) < getdate(contract.valid_from)
            or (contract.valid_to and getdate(doc.posting_date) > getdate(contract.valid_to))
        ):
            raise SaleFinancialError(f"CC Contract {lot.contract} is not active on the sale date")
        if contract.currency != doc.currency:
            raise SaleFinancialError(
                "Third-party Sales Invoice currency must match its Contract currency"
            )
        if Decimal(str(doc.conversion_rate or 0)) <= 0:
            raise SaleFinancialError("Third-party recognition requires a positive conversion rate")
        if relationship_model == "COMMISSION":
            commission_rate = Decimal(str(contract.commission_rate or 0))
        else:
            try:
                price_version = get_effective_price_version(
                    row.get(OWNERSHIP_FIELD),
                    _posting_datetime(doc),
                )
            except PriceResolutionError as exc:
                raise SaleFinancialError(str(exc)) from exc
            if price_version.currency != doc.currency:
                raise SaleFinancialError("CC Price Version currency differs from the sale currency")
            partner_rate = Decimal(str(price_version.partner_rate))
    financials = calculate_sale_financials(
        relationship_model=relationship_model,
        qty=Decimal(str(row.stock_qty or 0)),
        net_amount=Decimal(str(row.net_amount or 0)),
        commission_rate=commission_rate,
        partner_unit_rate=partner_rate,
        currency_precision=precision,
    )
    base_financials = convert_sale_financials_to_base(
        financials,
        conversion_rate=Decimal(str(doc.conversion_rate or 0)),
        currency_precision=int(row.precision("base_net_amount") or 2),
    )
    if Decimal(str(row.base_net_amount or 0)) != base_financials.gross_amount:
        raise SaleFinancialError(
            f"Sales Invoice row {row.idx} base amount differs from its controlled conversion"
        )
    return frappe._dict(
        lot=lot,
        contract=contract,
        price_version=price_version,
        financials=financials,
        base_financials=base_financials,
    )


def validate_sale_financials(doc: Any) -> None:
    """Prove accounts, contract and partner price before ERPNext starts posting."""
    import frappe

    mapping = (
        get_account_mapping(frappe, doc.company)
        if any(row.get(RELATIONSHIP_MODEL_FIELD) != "OWN" for row in doc.items)
        else None
    )
    company = frappe.get_cached_value(
        "Company",
        doc.company,
        ["default_income_account"],
        as_dict=True,
    )
    for row in doc.items:
        relationship_model = row.get(RELATIONSHIP_MODEL_FIELD)
        if relationship_model != "OWN":
            expected_income = mapping.gross_proceeds_clearing_account
        else:
            expected_income = company.default_income_account
        if row.income_account != expected_income:
            raise SaleFinancialError(
                f"Sales Invoice row {row.idx} income account changed from its controlled route"
            )
        _financial_context(frappe, doc, row)


def _insert_sale_allocation(frappe: Any, *, doc: Any, row: Any, context: Any) -> Any:
    existing = frappe.db.get_value(
        "CC Sale Allocation",
        {"sales_invoice_item": row.name},
        "name",
    )
    if existing:
        return frappe.get_doc("CC Sale Allocation", existing)
    allocation = frappe.get_doc("CC Allocation", row.get(ALLOCATION_FIELD))
    slices = {value.name: value for value in allocation.slices}
    allocation_slice = slices[row.get(ALLOCATION_SLICE_FIELD)]
    financials = context.financials
    base_financials = context.base_financials
    name = "CC-SALE-ALLOC-" + sha256(f"{doc.name}:{row.name}".encode()).hexdigest()[:20].upper()
    sale_allocation = frappe.get_doc(
        {
            "doctype": "CC Sale Allocation",
            "status": "SOLD",
            "sales_invoice": doc.name,
            "sales_invoice_item": row.name,
            "allocation": allocation.name,
            "allocation_slice": allocation_slice.name,
            "posting_date": doc.posting_date,
            "posting_datetime": _posting_datetime(doc),
            "company": doc.company,
            "customer": doc.customer,
            "item_code": row.item_code,
            "stock_lot": allocation_slice.stock_lot,
            "warehouse": allocation_slice.warehouse,
            "source_method": allocation_slice.source_method,
            "relationship_model": allocation_slice.relationship_model,
            "serial_no": allocation_slice.serial_no,
            "batch_no": allocation_slice.batch_no,
            "partner_profile": context.lot.partner_profile,
            "contract": context.lot.contract,
            "supplier": context.lot.supplier,
            "currency": doc.currency,
            "conversion_rate": doc.conversion_rate,
            "sold_qty": financials.qty,
            "net_rate": row.net_rate,
            "net_amount": financials.gross_amount,
            "commission_rate": financials.commission_rate,
            "commission_amount": financials.commission_amount,
            "price_version": context.price_version.name if context.price_version else None,
            "partner_unit_rate": financials.partner_unit_rate,
            "partner_amount": financials.partner_amount,
            "retained_amount": financials.retained_amount,
            "base_net_amount": base_financials.gross_amount,
            "base_commission_amount": base_financials.commission_amount,
            "base_partner_amount": base_financials.partner_amount,
            "base_retained_amount": base_financials.retained_amount,
            "returned_qty": 0,
            "settled_partner_amount": 0,
        }
    )
    with _sale_allocation_write(frappe):
        sale_allocation.insert(ignore_permissions=True, set_name=name)
    return sale_allocation


def _recognition_journal(frappe: Any, doc: Any, allocations: list[Any]) -> Any | None:
    third_party = [row for row in allocations if row.relationship_model != "OWN"]
    if not third_party:
        return None
    existing = frappe.db.get_value(
        "Journal Entry",
        {SALES_INVOICE_FIELD: doc.name, POSTING_KIND_FIELD: "SALE_RECOGNITION"},
        "name",
    )
    if existing:
        journal = frappe.get_doc("Journal Entry", existing)
        if journal.docstatus != 1:
            raise SaleFinancialError(f"Recognition Journal Entry {journal.name} is not submitted")
        return journal

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
        if partner <= 0 or retained < 0:
            raise SaleFinancialError(
                f"Third-party allocation {row.name} has invalid PSBO recognition amounts"
            )
        add(mapping.principal_proceeds_deduction_account, debit=partner)
        if retained:
            add(mapping.gross_proceeds_clearing_account, debit=retained)
            add(mapping.commission_revenue_account, credit=retained)
        liability = (
            mapping.unreported_commission_liability_account
            if row.relationship_model == "COMMISSION"
            else mapping.unreported_consignment_liability_account
        )
        add(liability, credit=partner)

    lines = [
        (account, amounts[0], amounts[1])
        for account, amounts in totals.items()
        if amounts[0] or amounts[1]
    ]
    debit = sum((line[1] for line in lines), Decimal("0"))
    credit = sum((line[2] for line in lines), Decimal("0"))
    if not lines or debit != credit:
        raise SaleFinancialError(
            f"Sale recognition is not balanced: debit={debit}, credit={credit}"
        )

    journal = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "company": doc.company,
            "posting_date": doc.posting_date,
            "voucher_type": "Journal Entry",
            "user_remark": f"CC sale recognition for Sales Invoice {doc.name}",
            SALES_INVOICE_FIELD: doc.name,
            POSTING_KIND_FIELD: "SALE_RECOGNITION",
        }
    )
    for account, debit_amount, credit_amount in lines:
        account_currency = frappe.db.get_value("Account", account, "account_currency")
        if account_currency != company.default_currency:
            raise SaleFinancialError(
                f"Recognition Account {account} must use Company base currency"
            )
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
    name = "CC-REC-" + sha256(doc.name.encode()).hexdigest()[:20].upper()
    journal.insert(ignore_permissions=True, set_name=name)
    journal.submit()
    return journal


def post_sale_allocations_and_recognition(doc: Any) -> list[Any]:
    """Persist exact sold slices and one balanced recognition JE after SI submit."""
    import frappe

    allocations = []
    for row in doc.items:
        context = _financial_context(frappe, doc, row)
        allocations.append(_insert_sale_allocation(frappe, doc=doc, row=row, context=context))
    journal = _recognition_journal(frappe, doc, allocations)
    if journal:
        for allocation in allocations:
            if allocation.relationship_model != "OWN":
                frappe.db.set_value(
                    "CC Sale Allocation",
                    allocation.name,
                    "recognition_journal_entry",
                    journal.name,
                    update_modified=False,
                )
                allocation.recognition_journal_entry = journal.name
    from .off_balance import post_sale_off_balance

    post_sale_off_balance(doc, allocations)
    return allocations


@contextmanager
def _recognition_cancellation(frappe: Any):
    previous = getattr(frappe.flags, RECOGNITION_CANCELLATION_FLAG, False)
    setattr(frappe.flags, RECOGNITION_CANCELLATION_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, RECOGNITION_CANCELLATION_FLAG, previous)


def cancel_sale_recognition(doc: Any) -> None:
    import frappe

    if not frappe.db.exists("CC Sale Allocation", {"sales_invoice": doc.name}):
        return
    reported = frappe.db.exists(
        "CC Sale Allocation",
        {
            "sales_invoice": doc.name,
            "settlement_report": ("is", "set"),
        },
    )
    if reported:
        frappe.throw("Cancel linked Settlement Reports before cancelling this managed Sales Invoice")
    journal_name = frappe.db.get_value(
        "Journal Entry",
        {SALES_INVOICE_FIELD: doc.name, POSTING_KIND_FIELD: "SALE_RECOGNITION"},
        "name",
    )
    if journal_name:
        journal = frappe.get_doc("Journal Entry", journal_name)
        if journal.docstatus == 1:
            with _recognition_cancellation(frappe):
                journal.cancel()


def mark_sale_allocations_cancelled(doc: Any) -> None:
    import frappe

    ignored = set(doc.get("ignore_linked_doctypes") or ())
    ignored.update({"CC Sale Allocation", "Journal Entry"})
    doc.ignore_linked_doctypes = tuple(sorted(ignored))
    for name in frappe.get_all(
        "CC Sale Allocation",
        filters={"sales_invoice": doc.name},
        pluck="name",
    ):
        frappe.db.set_value(
            "CC Sale Allocation",
            name,
            {"status": "CANCELLED", "cancellation_reason": "Sales Invoice cancelled"},
            update_modified=False,
        )


def guard_recognition_cancellation(doc: Any, method: str | None = None) -> None:
    del method
    import frappe

    if doc.get(SALES_INVOICE_FIELD) and not getattr(
        frappe.flags,
        RECOGNITION_CANCELLATION_FLAG,
        False,
    ):
        frappe.throw(
            f"Cancel managed Sales Invoice {doc.get(SALES_INVOICE_FIELD)} "
            "instead of its recognition Journal Entry"
        )
