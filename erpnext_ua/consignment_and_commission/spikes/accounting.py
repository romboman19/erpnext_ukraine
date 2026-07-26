"""Test-site-only JE, settlement, Payment Entry and currency probes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..services.accounting import (
    AccountingPlan,
    AccountingPlanError,
    build_commission_recognition,
    build_consignment_recognition,
    build_settlement_debt,
    calculate_currency_outstanding,
    calculate_exchange_difference,
    calculate_payment_base_amount,
    resolve_adjustment_posting_date,
    validate_payment_report_binding,
)
from .inventory_dimension import _assert_test_scope

REPORT_DOCTYPE = "TP Spike Settlement Report"
REPORT_FIELD = "tp_spike_settlement_report"
POSTING_KIND_FIELD = "tp_spike_posting_kind"
ECONOMIC_DATE_FIELD = "tp_spike_economic_date"

ACCOUNT_TITLES = {
    "commission_gross_proceeds": "TP Commission Gross Proceeds",
    "commission_revenue": "TP Commission Revenue",
    "principal_proceeds_deduction": "TP Principal Proceeds Deduction",
    "commission_loss": "TP Commission Loss",
    "unreported_commission_liability": "TP Unreported Commission Liability",
    "consignment_cogs": "TP Consignment COGS",
    "unreported_consignment_liability": "TP Unreported Consignment Liability",
    "off_balance_goods": "TP Goods Accepted on Commission",
    "supplier_payable_usd": "TP Creditors USD",
}

SUPPLIER_NAMES = {
    "UAH": "TP Gate 0D Supplier UAH",
    "USD": "TP Gate 0D Supplier USD",
}


def _ensure_report_doctype(frappe: Any) -> None:
    if frappe.db.exists("DocType", REPORT_DOCTYPE):
        return

    frappe.get_doc(
        {
            "doctype": "DocType",
            "name": REPORT_DOCTYPE,
            "module": "Consignment and Commission",
            "custom": 1,
            "is_submittable": 1,
            "autoname": "field:report_id",
            "naming_rule": "By fieldname",
            "fields": [
                {
                    "fieldname": "report_id",
                    "label": "Report ID",
                    "fieldtype": "Data",
                    "reqd": 1,
                    "unique": 1,
                    "in_list_view": 1,
                },
                {
                    "fieldname": "company",
                    "label": "Company",
                    "fieldtype": "Link",
                    "options": "Company",
                    "reqd": 1,
                },
                {
                    "fieldname": "supplier",
                    "label": "Supplier",
                    "fieldtype": "Link",
                    "options": "Supplier",
                    "reqd": 1,
                },
                {
                    "fieldname": "relationship_model",
                    "label": "Relationship Model",
                    "fieldtype": "Select",
                    "options": "COMMISSION\nCONSIGNMENT",
                    "reqd": 1,
                },
                {
                    "fieldname": "currency",
                    "label": "Currency",
                    "fieldtype": "Link",
                    "options": "Currency",
                    "reqd": 1,
                },
                {
                    "fieldname": "partner_amount",
                    "label": "Partner Amount",
                    "fieldtype": "Currency",
                    "options": "currency",
                    "reqd": 1,
                },
                {
                    "fieldname": "provisional_exchange_rate",
                    "label": "Provisional Exchange Rate",
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
                    "submit": 1,
                    "cancel": 1,
                }
            ],
        }
    ).insert(ignore_permissions=True)


def _ensure_custom_fields(frappe: Any) -> None:
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            "Journal Entry": [
                {
                    "fieldname": REPORT_FIELD,
                    "label": "TP Spike Settlement Report",
                    "fieldtype": "Link",
                    "options": REPORT_DOCTYPE,
                    "read_only": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": POSTING_KIND_FIELD,
                    "label": "TP Spike Posting Kind",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": ECONOMIC_DATE_FIELD,
                    "label": "TP Spike Economic Date",
                    "fieldtype": "Date",
                    "read_only": 1,
                },
            ],
            "Payment Entry": [
                {
                    "fieldname": REPORT_FIELD,
                    "label": "TP Spike Settlement Report",
                    "fieldtype": "Link",
                    "options": REPORT_DOCTYPE,
                    "read_only": 1,
                    "search_index": 1,
                }
            ],
        }
    )


def _parent_account(frappe: Any, company: str, account_name: str) -> str:
    parent = frappe.db.get_value(
        "Account",
        {"company": company, "account_name": account_name, "is_group": 1},
        "name",
    )
    if not parent:
        raise RuntimeError(f"Missing test Company parent account {account_name!r}")
    return parent


def _ensure_account(
    frappe: Any,
    *,
    company: str,
    account_name: str,
    parent_name: str,
    currency: str,
    account_type: str | None = None,
    account_number: str | None = None,
    off_balance: bool = False,
) -> str:
    filters = (
        {"company": company, "account_number": account_number, "is_group": 0}
        if account_number
        else {"company": company, "account_name": account_name}
    )
    existing = frappe.db.get_value(
        "Account",
        filters,
        ["name", "account_currency", "account_type", "disabled", "ua_off_balance"],
        as_dict=True,
    )
    if existing:
        if (
            existing.account_currency != currency
            or (account_type and existing.account_type != account_type)
            or (off_balance and (not existing.disabled or not existing.ua_off_balance))
        ):
            raise RuntimeError(f"Existing test account {existing.name!r} has incompatible settings")
        return existing.name

    account = frappe.get_doc(
        {
            "doctype": "Account",
            "company": company,
            "account_name": account_name,
            "parent_account": _parent_account(frappe, company, parent_name),
            "is_group": 0,
            "account_currency": currency,
            "account_type": account_type,
            "account_number": account_number,
            "disabled": int(off_balance),
            "ua_off_balance": int(off_balance),
        }
    )
    account.insert(ignore_permissions=True)
    return account.name


def _ensure_accounts(
    frappe: Any,
    company: str,
    *,
    require_payment_accounts: bool = True,
) -> dict[str, str]:
    company_doc = frappe.get_cached_doc("Company", company)
    currency = company_doc.default_currency
    specifications = {
        "commission_gross_proceeds": ("Direct Income", currency, None, "702", False),
        "commission_revenue": ("Direct Income", currency, None, "703", False),
        "principal_proceeds_deduction": ("Direct Income", currency, None, "704", False),
        "commission_loss": ("Indirect Expenses", currency, None, None, False),
        "unreported_commission_liability": (
            "Current Liabilities",
            currency,
            None,
            "6851",
            False,
        ),
        "consignment_cogs": ("Stock Expenses", currency, None, None, False),
        "unreported_consignment_liability": (
            "Current Liabilities",
            currency,
            None,
            "6852",
            False,
        ),
        "off_balance_goods": ("Current Assets", currency, None, "024", True),
        "supplier_payable_usd": ("Accounts Payable", "USD", "Payable", None, False),
    }
    accounts = {
        key: _ensure_account(
            frappe,
            company=company,
            account_name=ACCOUNT_TITLES[key],
            parent_name=parent,
            currency=account_currency,
            account_type=account_type,
            account_number=account_number,
            off_balance=off_balance,
        )
        for key, (
            parent,
            account_currency,
            account_type,
            account_number,
            off_balance,
        ) in specifications.items()
    }
    accounts.update(
        {
            "gross_sales_reclassification": accounts["commission_gross_proceeds"],
            "agency_service_revenue": accounts["commission_revenue"],
            "agency_loss": accounts["commission_loss"],
        }
    )
    accounts["supplier_payable"] = company_doc.default_payable_account
    accounts["bank"] = company_doc.default_bank_account
    accounts["exchange_gain_loss"] = company_doc.exchange_gain_loss_account
    required = {
        "commission_gross_proceeds",
        "commission_revenue",
        "principal_proceeds_deduction",
        "unreported_commission_liability",
        "unreported_consignment_liability",
        "off_balance_goods",
        "supplier_payable",
    }
    if require_payment_accounts:
        required.update({"bank", "exchange_gain_loss"})
    missing = sorted(key for key in required if not accounts[key])
    if missing:
        raise RuntimeError(f"Company accounting setup is missing: {', '.join(missing)}")
    return accounts


def _ensure_supplier(frappe: Any, *, company: str, supplier_name: str, payable_account: str) -> str:
    existing = frappe.db.get_value("Supplier", {"supplier_name": supplier_name}, "name")
    supplier = frappe.get_doc("Supplier", existing) if existing else frappe.new_doc("Supplier")
    account_currency = frappe.db.get_value("Account", payable_account, "account_currency")
    if not existing:
        supplier.supplier_name = supplier_name
        supplier.supplier_type = "Company"
        supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
        if not supplier_group:
            raise RuntimeError("A leaf Supplier Group is required for the Gate 0D fixture")
        supplier.supplier_group = supplier_group
    if supplier.default_currency and supplier.default_currency != account_currency:
        raise RuntimeError(f"Existing test Supplier {supplier.name!r} uses another billing currency")
    supplier.default_currency = account_currency

    company_rows = [row for row in supplier.get("accounts") if row.company == company]
    if company_rows:
        if company_rows[0].account != payable_account:
            raise RuntimeError(f"Existing test Supplier {supplier.name!r} uses another payable account")
    else:
        supplier.append("accounts", {"company": company, "account": payable_account})

    if existing:
        supplier.save(ignore_permissions=True)
    else:
        supplier.insert(ignore_permissions=True)
    return supplier.name


def _make_report(
    frappe: Any,
    *,
    report_id: str,
    company: str,
    supplier: str,
    relationship_model: str,
    currency: str,
    partner_amount: Decimal,
    provisional_exchange_rate: Decimal,
) -> Any:
    report = frappe.get_doc(
        {
            "doctype": REPORT_DOCTYPE,
            "report_id": report_id,
            "company": company,
            "supplier": supplier,
            "relationship_model": relationship_model,
            "currency": currency,
            "partner_amount": float(partner_amount),
            "provisional_exchange_rate": float(provisional_exchange_rate),
        }
    )
    report.insert(ignore_permissions=True)
    report.submit()
    return report


def _make_plan_journal_entry(
    frappe: Any,
    *,
    company: str,
    accounts: dict[str, str],
    plan: AccountingPlan,
    posting_date: str,
    report_name: str | None = None,
    economic_date: str | None = None,
) -> Any:
    company_doc = frappe.get_cached_doc("Company", company)
    journal = frappe.new_doc("Journal Entry")
    journal.company = company
    journal.posting_date = posting_date
    journal.voucher_type = "Journal Entry"
    journal.user_remark = f"Gate 0D: {plan.kind}"
    journal.set(POSTING_KIND_FIELD, plan.kind)
    if report_name:
        journal.set(REPORT_FIELD, report_name)
    if economic_date:
        journal.set(ECONOMIC_DATE_FIELD, economic_date)

    for line in plan.lines:
        account = accounts[line.account_key]
        account_currency = frappe.db.get_value("Account", account, "account_currency")
        if account_currency != company_doc.default_currency:
            raise RuntimeError("Base-currency plan cannot post to a foreign-currency account")
        row = {
            "account": account,
            "debit_in_account_currency": float(line.debit),
            "credit_in_account_currency": float(line.credit),
            "exchange_rate": 1,
            "cost_center": company_doc.cost_center,
        }
        if line.party:
            row.update({"party_type": line.party_type, "party": line.party})
        # The standard JEA reference must stay empty. A custom report reference on
        # the parent keeps the Supplier row matchable by a standard Payment Entry.
        journal.append("accounts", row)

    journal.insert(ignore_permissions=True)
    journal.submit()
    return journal


def _make_foreign_debt_journal_entry(
    frappe: Any,
    *,
    company: str,
    accounts: dict[str, str],
    supplier: str,
    report_name: str,
    obligation_amount: Decimal,
    provisional_exchange_rate: Decimal,
    posting_date: str,
) -> Any:
    company_doc = frappe.get_cached_doc("Company", company)
    base_amount = obligation_amount * provisional_exchange_rate
    journal = frappe.new_doc("Journal Entry")
    journal.company = company
    journal.posting_date = posting_date
    journal.voucher_type = "Journal Entry"
    journal.multi_currency = 1
    journal.user_remark = "Gate 0D: foreign-currency settlement debt"
    journal.set(POSTING_KIND_FIELD, "SETTLEMENT_DEBT")
    journal.set(REPORT_FIELD, report_name)
    journal.append(
        "accounts",
        {
            "account": accounts["unreported_consignment_liability"],
            "debit_in_account_currency": float(base_amount),
            "credit_in_account_currency": 0,
            "exchange_rate": 1,
            "cost_center": company_doc.cost_center,
        },
    )
    journal.append(
        "accounts",
        {
            "account": accounts["supplier_payable_usd"],
            "party_type": "Supplier",
            "party": supplier,
            "debit_in_account_currency": 0,
            "credit_in_account_currency": float(obligation_amount),
            "exchange_rate": float(provisional_exchange_rate),
            "cost_center": company_doc.cost_center,
        },
    )
    journal.insert(ignore_permissions=True)
    journal.submit()
    return journal


def _referenced_reports(frappe: Any, payment: Any) -> list[str]:
    return [
        frappe.db.get_value("Journal Entry", row.reference_name, REPORT_FIELD)
        for row in payment.references
        if row.reference_doctype == "Journal Entry" and row.reference_name
    ]


def _validate_payment_binding(frappe: Any, payment: Any) -> str:
    return validate_payment_report_binding(
        payment.get(REPORT_FIELD),
        _referenced_reports(frappe, payment),
    )


def _make_payment_entry(
    frappe: Any,
    *,
    company: str,
    supplier: str,
    payable_account: str,
    bank_account: str,
    report_name: str,
    debt_journal: str,
    obligation_amount: Decimal,
    outstanding_amount: Decimal,
    total_amount: Decimal,
    payment_exchange_rate: Decimal,
    posting_date: str,
    sequence: int,
) -> Any:
    company_doc = frappe.get_cached_doc("Company", company)
    party_currency = frappe.db.get_value("Account", payable_account, "account_currency")
    base_amount = calculate_payment_base_amount(obligation_amount, payment_exchange_rate)

    payment = frappe.new_doc("Payment Entry")
    payment.payment_type = "Pay"
    payment.company = company
    payment.posting_date = posting_date
    payment.party_type = "Supplier"
    payment.party = supplier
    payment.paid_from = bank_account
    payment.paid_to = payable_account
    payment.paid_from_account_currency = company_doc.default_currency
    payment.paid_to_account_currency = party_currency
    payment.paid_from_account_type = frappe.db.get_value("Account", bank_account, "account_type")
    payment.paid_to_account_type = frappe.db.get_value("Account", payable_account, "account_type")
    payment.paid_amount = float(base_amount)
    payment.received_amount = float(obligation_amount)
    payment.source_exchange_rate = 1
    payment.target_exchange_rate = float(payment_exchange_rate)
    payment.reference_no = f"TP-GATE-0D-{report_name}-{sequence}"
    payment.reference_date = posting_date
    payment.cost_center = company_doc.cost_center
    payment.set(REPORT_FIELD, report_name)
    payment.append(
        "references",
        {
            "reference_doctype": "Journal Entry",
            "reference_name": debt_journal,
            "total_amount": float(total_amount),
            "outstanding_amount": float(outstanding_amount),
            "allocated_amount": float(obligation_amount),
        },
    )
    _validate_payment_binding(frappe, payment)
    payment.insert(ignore_permissions=True)
    payment.submit()
    return payment


def _ensure_exchange_rate(
    frappe: Any,
    *,
    posting_date: str,
    exchange_rate: Decimal,
) -> tuple[str, bool]:
    existing = frappe.db.get_value(
        "Currency Exchange",
        {
            "date": posting_date,
            "from_currency": "USD",
            "to_currency": "UAH",
            "for_buying": 1,
            "for_selling": 1,
        },
        ["name", "exchange_rate"],
        as_dict=True,
    )
    if existing:
        actual = Decimal(str(existing.exchange_rate))
        if actual != exchange_rate:
            raise RuntimeError(f"Currency Exchange {existing.name!r} already exists with rate {actual}")
        return existing.name, False

    document = frappe.get_doc(
        {
            "doctype": "Currency Exchange",
            "date": posting_date,
            "from_currency": "USD",
            "to_currency": "UAH",
            "exchange_rate": float(exchange_rate),
            "for_buying": 1,
            "for_selling": 1,
        }
    )
    document.insert(ignore_permissions=True)
    return document.name, True


def _gl_evidence(frappe: Any, voucher_no: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        "GL Entry",
        filters={"voucher_no": voucher_no},
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
            "is_cancelled",
        ],
        order_by="creation asc, name asc",
    )


def _payment_ledger_evidence(frappe: Any, voucher_names: list[str]) -> list[dict[str, Any]]:
    return frappe.get_all(
        "Payment Ledger Entry",
        filters={"voucher_no": ["in", voucher_names]},
        fields=[
            "account",
            "party_type",
            "party",
            "voucher_type",
            "voucher_no",
            "against_voucher_type",
            "against_voucher_no",
            "amount",
            "amount_in_account_currency",
            "delinked",
        ],
        order_by="creation asc, name asc",
    )


def _outstanding(frappe: Any, journal_name: str, supplier: str) -> dict[str, float]:
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_outstanding_on_journal_entry

    outstanding, total = get_outstanding_on_journal_entry(journal_name, "Supplier", supplier)
    return {"outstanding": float(outstanding or 0), "total": float(total or 0)}


def _exchange_journal_evidence(frappe: Any, payment_names: list[str]) -> list[dict[str, Any]]:
    parent_names = frappe.get_all(
        "Journal Entry Account",
        filters={"reference_type": "Payment Entry", "reference_name": ["in", payment_names]},
        pluck="parent",
    )
    if not parent_names:
        return []
    documents = frappe.get_all(
        "Journal Entry",
        filters={"name": ["in", sorted(set(parent_names))]},
        fields=["name", "voucher_type", "posting_date", "docstatus", "multi_currency", "is_system_generated"],
        order_by="creation asc, name asc",
    )
    for document in documents:
        document["gl"] = _gl_evidence(frappe, document.name)
    return documents


def _cancel_submitted(documents: list[Any]) -> tuple[list[str], list[str]]:
    cancelled = []
    errors = []
    for document in reversed(documents):
        try:
            document.reload()
            if document.docstatus == 1:
                document.cancel()
                cancelled.append(document.name)
        except Exception as exc:  # pragma: no cover - evidence is returned by the integration runner
            errors.append(f"{document.doctype} {document.name}: {type(exc).__name__}: {exc}")
    return cancelled, errors


def run_accounting_flow(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Verify recognition, report debt, partial PE, currency and cancellation behavior."""
    import frappe
    from frappe.utils import add_days, getdate, nowdate

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")
    company_doc = frappe.get_cached_doc("Company", company)
    if company_doc.default_currency != "UAH":
        raise RuntimeError("Gate 0D fixture expects a UAH test Company")

    _ensure_report_doctype(frappe)
    _ensure_custom_fields(frappe)
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

    report_date = add_days(nowdate(), -2)
    first_payment_date = add_days(nowdate(), -1)
    second_payment_date = nowdate()
    rate_specs = [
        (report_date, Decimal("40")),
        (first_payment_date, Decimal("41.20")),
        (second_payment_date, Decimal("41.50")),
    ]
    exchange_rates = []
    created_exchange_rates = []
    for posting_date, exchange_rate in rate_specs:
        name, created = _ensure_exchange_rate(
            frappe,
            posting_date=posting_date,
            exchange_rate=exchange_rate,
        )
        exchange_rates.append({"name": name, "date": posting_date, "rate": float(exchange_rate)})
        if created:
            created_exchange_rates.append(name)
    frappe.db.commit()

    recognition_documents: list[Any] = []
    report_documents: list[Any] = []
    debt_documents: list[Any] = []
    payment_documents: list[Any] = []
    adjustment_documents: list[Any] = []
    result: dict[str, Any] = {
        "site": frappe.local.site,
        "company": company,
        "accounts": accounts,
        "suppliers": suppliers,
        "exchange_rates": exchange_rates,
    }
    run_id = frappe.generate_hash(length=10).upper()
    exchange_journal_names: list[str] = []

    try:
        commission_recognition = _make_plan_journal_entry(
            frappe,
            company=company,
            accounts=accounts,
            plan=build_commission_recognition("10000", "8500"),
            posting_date=report_date,
        )
        recognition_documents.append(commission_recognition)

        consignment_recognition_uah = _make_plan_journal_entry(
            frappe,
            company=company,
            accounts=accounts,
            plan=build_consignment_recognition("10000", "8000"),
            posting_date=report_date,
        )
        recognition_documents.append(consignment_recognition_uah)

        consignment_recognition_usd = _make_plan_journal_entry(
            frappe,
            company=company,
            accounts=accounts,
            plan=build_consignment_recognition("10000", "8000"),
            posting_date=report_date,
        )
        recognition_documents.append(consignment_recognition_usd)

        report_uah = _make_report(
            frappe,
            report_id=f"TP-GATE-0D-UAH-{run_id}",
            company=company,
            supplier=suppliers["UAH"],
            relationship_model="COMMISSION",
            currency="UAH",
            partner_amount=Decimal("8500"),
            provisional_exchange_rate=Decimal("1"),
        )
        report_documents.append(report_uah)
        debt_uah = _make_plan_journal_entry(
            frappe,
            company=company,
            accounts=accounts,
            plan=build_settlement_debt(
                "8500",
                relationship_model="COMMISSION",
                supplier=suppliers["UAH"],
                report_doctype=REPORT_DOCTYPE,
                report_name=report_uah.name,
            ),
            posting_date=report_date,
            report_name=report_uah.name,
        )
        debt_documents.append(debt_uah)

        report_uah_second = _make_report(
            frappe,
            report_id=f"TP-GATE-0D-UAH-SECOND-{run_id}",
            company=company,
            supplier=suppliers["UAH"],
            relationship_model="CONSIGNMENT",
            currency="UAH",
            partner_amount=Decimal("8000"),
            provisional_exchange_rate=Decimal("1"),
        )
        report_documents.append(report_uah_second)
        debt_uah_second = _make_plan_journal_entry(
            frappe,
            company=company,
            accounts=accounts,
            plan=build_settlement_debt(
                "8000",
                relationship_model="CONSIGNMENT",
                supplier=suppliers["UAH"],
                report_doctype=REPORT_DOCTYPE,
                report_name=report_uah_second.name,
            ),
            posting_date=report_date,
            report_name=report_uah_second.name,
        )
        debt_documents.append(debt_uah_second)

        invalid_payment = frappe.new_doc("Payment Entry")
        invalid_payment.set(REPORT_FIELD, report_uah.name)
        invalid_payment.append(
            "references", {"reference_doctype": "Journal Entry", "reference_name": debt_uah.name}
        )
        invalid_payment.append(
            "references",
            {"reference_doctype": "Journal Entry", "reference_name": debt_uah_second.name},
        )
        try:
            _validate_payment_binding(frappe, invalid_payment)
        except AccountingPlanError as exc:
            result["multiple_report_guard"] = {"status": "PASS", "error": str(exc)}
        else:
            raise AssertionError("Payment Entry guard accepted references from multiple reports")

        uah_payment_1 = _make_payment_entry(
            frappe,
            company=company,
            supplier=suppliers["UAH"],
            payable_account=accounts["supplier_payable"],
            bank_account=accounts["bank"],
            report_name=report_uah.name,
            debt_journal=debt_uah.name,
            obligation_amount=Decimal("3000"),
            outstanding_amount=Decimal("8500"),
            total_amount=Decimal("8500"),
            payment_exchange_rate=Decimal("1"),
            posting_date=first_payment_date,
            sequence=1,
        )
        payment_documents.append(uah_payment_1)
        uah_outstanding_after_first = _outstanding(frappe, debt_uah.name, suppliers["UAH"])

        uah_payment_2 = _make_payment_entry(
            frappe,
            company=company,
            supplier=suppliers["UAH"],
            payable_account=accounts["supplier_payable"],
            bank_account=accounts["bank"],
            report_name=report_uah.name,
            debt_journal=debt_uah.name,
            obligation_amount=Decimal("5500"),
            outstanding_amount=Decimal("5500"),
            total_amount=Decimal("8500"),
            payment_exchange_rate=Decimal("1"),
            posting_date=second_payment_date,
            sequence=2,
        )
        payment_documents.append(uah_payment_2)
        uah_outstanding_after_second = _outstanding(frappe, debt_uah.name, suppliers["UAH"])

        report_usd = _make_report(
            frappe,
            report_id=f"TP-GATE-0D-USD-{run_id}",
            company=company,
            supplier=suppliers["USD"],
            relationship_model="CONSIGNMENT",
            currency="USD",
            partner_amount=Decimal("200"),
            provisional_exchange_rate=Decimal("40"),
        )
        report_documents.append(report_usd)
        debt_usd = _make_foreign_debt_journal_entry(
            frappe,
            company=company,
            accounts=accounts,
            supplier=suppliers["USD"],
            report_name=report_usd.name,
            obligation_amount=Decimal("200"),
            provisional_exchange_rate=Decimal("40"),
            posting_date=report_date,
        )
        debt_documents.append(debt_usd)

        usd_payment_1 = _make_payment_entry(
            frappe,
            company=company,
            supplier=suppliers["USD"],
            payable_account=accounts["supplier_payable_usd"],
            bank_account=accounts["bank"],
            report_name=report_usd.name,
            debt_journal=debt_usd.name,
            obligation_amount=Decimal("100"),
            outstanding_amount=Decimal("200"),
            total_amount=Decimal("200"),
            payment_exchange_rate=Decimal("41.20"),
            posting_date=first_payment_date,
            sequence=1,
        )
        payment_documents.append(usd_payment_1)
        usd_outstanding_after_first = _outstanding(frappe, debt_usd.name, suppliers["USD"])

        usd_payment_2 = _make_payment_entry(
            frappe,
            company=company,
            supplier=suppliers["USD"],
            payable_account=accounts["supplier_payable_usd"],
            bank_account=accounts["bank"],
            report_name=report_usd.name,
            debt_journal=debt_usd.name,
            obligation_amount=Decimal("100"),
            outstanding_amount=Decimal("100"),
            total_amount=Decimal("200"),
            payment_exchange_rate=Decimal("41.50"),
            posting_date=second_payment_date,
            sequence=2,
        )
        payment_documents.append(usd_payment_2)
        usd_outstanding_after_second = _outstanding(frappe, debt_usd.name, suppliers["USD"])

        economic_date = getdate(add_days(nowdate(), -40))
        closed_through = getdate(add_days(nowdate(), -10))
        adjustment_posting_date = resolve_adjustment_posting_date(economic_date, closed_through)
        backdated_adjustment = _make_plan_journal_entry(
            frappe,
            company=company,
            accounts=accounts,
            plan=build_consignment_recognition("5", "3"),
            posting_date=str(adjustment_posting_date),
            economic_date=str(economic_date),
        )
        adjustment_documents.append(backdated_adjustment)

        payment_names = [payment.name for payment in payment_documents]
        exchange_journals = _exchange_journal_evidence(
            frappe, [usd_payment_1.name, usd_payment_2.name]
        )
        exchange_journal_names = [journal["name"] for journal in exchange_journals]
        result.update(
            {
                "recognition": {
                    "commission": {
                        "journal_entry": commission_recognition.name,
                        "gl": _gl_evidence(frappe, commission_recognition.name),
                    },
                    "consignment_uah": {
                        "journal_entry": consignment_recognition_uah.name,
                        "gl": _gl_evidence(frappe, consignment_recognition_uah.name),
                    },
                    "consignment_usd_base": {
                        "journal_entry": consignment_recognition_usd.name,
                        "gl": _gl_evidence(frappe, consignment_recognition_usd.name),
                    },
                },
                "uah_settlement": {
                    "report": report_uah.name,
                    "debt_journal_entry": debt_uah.name,
                    "debt_gl": _gl_evidence(frappe, debt_uah.name),
                    "payment_entries": [uah_payment_1.name, uah_payment_2.name],
                    "payment_gl": {
                        uah_payment_1.name: _gl_evidence(frappe, uah_payment_1.name),
                        uah_payment_2.name: _gl_evidence(frappe, uah_payment_2.name),
                    },
                    "outstanding_after_first": uah_outstanding_after_first,
                    "outstanding_after_second": uah_outstanding_after_second,
                    "currency_outstanding_after_first": float(
                        calculate_currency_outstanding("8500", payments="3000")
                    ),
                    "currency_outstanding_after_second": float(
                        calculate_currency_outstanding("8500", payments="8500")
                    ),
                },
                "usd_settlement": {
                    "report": report_usd.name,
                    "debt_journal_entry": debt_usd.name,
                    "debt_gl": _gl_evidence(frappe, debt_usd.name),
                    "payment_entries": [usd_payment_1.name, usd_payment_2.name],
                    "payment_gl": {
                        usd_payment_1.name: _gl_evidence(frappe, usd_payment_1.name),
                        usd_payment_2.name: _gl_evidence(frappe, usd_payment_2.name),
                    },
                    "outstanding_after_first": usd_outstanding_after_first,
                    "outstanding_after_second": usd_outstanding_after_second,
                    "base_paid": [
                        float(calculate_payment_base_amount("100", "41.20")),
                        float(calculate_payment_base_amount("100", "41.50")),
                    ],
                    "expected_exchange_difference": [
                        float(calculate_exchange_difference("100", "40", "41.20")),
                        float(calculate_exchange_difference("100", "40", "41.50")),
                    ],
                    "exchange_journals": exchange_journals,
                },
                "payment_ledger": _payment_ledger_evidence(
                    frappe,
                    [debt_uah.name, debt_usd.name, *payment_names],
                ),
                "backdated_adjustment": {
                    "journal_entry": backdated_adjustment.name,
                    "economic_date": str(economic_date),
                    "closed_through": str(closed_through),
                    "posting_date": str(adjustment_posting_date),
                    "gl": _gl_evidence(frappe, backdated_adjustment.name),
                },
            }
        )
    finally:
        cancelled_payments, payment_cancel_errors = _cancel_submitted(payment_documents)
        result["outstanding_after_payment_cancel"] = {
            debt.name: _outstanding(frappe, debt.name, debt.accounts[-1].party)
            for debt in debt_documents
            if debt.accounts and debt.accounts[-1].party
        }
        result["exchange_journals_after_payment_cancel"] = (
            frappe.get_all(
                "Journal Entry",
                filters={"name": ["in", exchange_journal_names]},
                fields=["name", "docstatus", "voucher_type", "is_system_generated"],
                order_by="name asc",
            )
            if exchange_journal_names
            else []
        )
        cancelled_adjustments, adjustment_cancel_errors = _cancel_submitted(adjustment_documents)
        cancelled_debts, debt_cancel_errors = _cancel_submitted(debt_documents)
        cancelled_reports, report_cancel_errors = _cancel_submitted(report_documents)
        cancelled_recognition, recognition_cancel_errors = _cancel_submitted(recognition_documents)
        result["cancellation"] = {
            "order": ["Payment Entry", "Adjustment JE", "Debt JE", "Report", "Recognition JE"],
            "payments": cancelled_payments,
            "adjustments": cancelled_adjustments,
            "debts": cancelled_debts,
            "reports": cancelled_reports,
            "recognition": cancelled_recognition,
            "errors": [
                *payment_cancel_errors,
                *adjustment_cancel_errors,
                *debt_cancel_errors,
                *report_cancel_errors,
                *recognition_cancel_errors,
            ],
        }
        for exchange_rate_name in created_exchange_rates:
            if frappe.db.exists("Currency Exchange", exchange_rate_name):
                frappe.delete_doc("Currency Exchange", exchange_rate_name, ignore_permissions=True)
        frappe.db.commit()

    if result["cancellation"]["errors"]:
        raise AssertionError(f"Gate 0D cleanup failed: {result['cancellation']['errors']}")
    if abs(result["uah_settlement"]["outstanding_after_first"]["outstanding"]) != 5500:
        raise AssertionError(f"UAH partial payment outstanding is incorrect: {result}")
    if abs(result["uah_settlement"]["outstanding_after_second"]["outstanding"]) != 0:
        raise AssertionError(f"UAH report did not close after two payments: {result}")
    if abs(result["usd_settlement"]["outstanding_after_first"]["outstanding"]) != 100:
        raise AssertionError(f"USD partial payment outstanding is incorrect: {result}")
    if abs(result["usd_settlement"]["outstanding_after_second"]["outstanding"]) != 0:
        raise AssertionError(f"USD report did not close in obligation currency: {result}")
    if not result["usd_settlement"]["exchange_journals"]:
        raise AssertionError(f"ERPNext did not create exchange gain/loss Journal Entries: {result}")
    expected_restored = {
        result["uah_settlement"]["debt_journal_entry"]: 8500,
        result["usd_settlement"]["debt_journal_entry"]: 200,
    }
    for journal_name, expected_outstanding in expected_restored.items():
        actual_outstanding = abs(result["outstanding_after_payment_cancel"][journal_name]["outstanding"])
        if actual_outstanding != expected_outstanding:
            raise AssertionError(
                f"Cancel Payment Entry did not restore {journal_name} outstanding: "
                f"actual={actual_outstanding}, expected={expected_outstanding}"
            )
    if any(journal["docstatus"] != 2 for journal in result["exchange_journals_after_payment_cancel"]):
        raise AssertionError(f"Exchange gain/loss Journal Entry was not cancelled with Payment Entry: {result}")
    if result["backdated_adjustment"]["economic_date"] == result["backdated_adjustment"]["posting_date"]:
        raise AssertionError(f"Backdated adjustment was posted into its closed economic date: {result}")
    return result
