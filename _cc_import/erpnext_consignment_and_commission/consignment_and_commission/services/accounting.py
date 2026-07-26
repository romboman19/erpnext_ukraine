"""Pure accounting plans for third-party sales and settlements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

PostingKind = Literal["COMMISSION_RECOGNITION", "CONSIGNMENT_RECOGNITION", "SETTLEMENT_DEBT"]


class AccountingPlanError(ValueError):
    """Raised when a third-party accounting plan would violate an invariant."""


@dataclass(frozen=True, slots=True)
class AccountingLine:
    account_key: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    party_type: str | None = None
    party: str | None = None
    reference_doctype: str | None = None
    reference_name: str | None = None


@dataclass(frozen=True, slots=True)
class AccountingPlan:
    kind: PostingKind
    lines: tuple[AccountingLine, ...]

    @property
    def total_debit(self) -> Decimal:
        return sum((line.debit for line in self.lines), Decimal("0"))

    @property
    def total_credit(self) -> Decimal:
        return sum((line.credit for line in self.lines), Decimal("0"))


def _amount(value: Decimal | str | int | float, *, label: str) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise AccountingPlanError(f"{label} must be finite")
    return amount


def _validate_plan(plan: AccountingPlan) -> AccountingPlan:
    if not plan.lines:
        raise AccountingPlanError("Accounting plan must contain at least one line")
    for line in plan.lines:
        if line.debit < 0 or line.credit < 0:
            raise AccountingPlanError("Accounting line values cannot be negative")
        if bool(line.debit) == bool(line.credit):
            raise AccountingPlanError("Each accounting line must contain exactly one debit or credit")
        if bool(line.party_type) != bool(line.party):
            raise AccountingPlanError("Party type and party must be specified together")
        if bool(line.reference_doctype) != bool(line.reference_name):
            raise AccountingPlanError("Reference doctype and name must be specified together")
    if plan.total_debit != plan.total_credit:
        raise AccountingPlanError(
            f"Accounting plan is not balanced: debit={plan.total_debit}, credit={plan.total_credit}"
        )
    return plan


def build_commission_recognition(
    gross_amount: Decimal | str | int | float,
    partner_amount: Decimal | str | int | float,
    *,
    allow_negative_margin: bool = False,
) -> AccountingPlan:
    """Present only the commission fee as revenue under the Ukrainian PSBO model.

    The customer-facing Sales Invoice first credits gross goods revenue.  This
    plan reclassifies the store's fee to service revenue, records the amount
    belonging to the principal as a deduction from revenue, and recognizes the
    matching creditor balance.
    """
    gross = _amount(gross_amount, label="Gross amount")
    partner = _amount(partner_amount, label="Partner amount")
    if gross <= 0:
        raise AccountingPlanError("Gross amount must be greater than zero")
    if partner < 0:
        raise AccountingPlanError("Partner amount cannot be negative")

    margin = gross - partner
    if margin < 0 and not allow_negative_margin:
        raise AccountingPlanError("Partner amount above gross proceeds requires loss approval")

    deduction = min(gross, partner)
    lines = []
    if deduction:
        lines.append(AccountingLine("principal_proceeds_deduction", debit=deduction))
    if margin > 0:
        lines.extend(
            (
                AccountingLine("gross_sales_reclassification", debit=margin),
                AccountingLine("agency_service_revenue", credit=margin),
            )
        )
    elif margin < 0:
        lines.append(AccountingLine("agency_loss", debit=-margin))
    if partner:
        lines.append(AccountingLine("unreported_commission_liability", credit=partner))
    return _validate_plan(AccountingPlan("COMMISSION_RECOGNITION", tuple(lines)))


def build_consignment_recognition(
    gross_amount: Decimal | str | int | float,
    partner_amount: Decimal | str | int | float,
    *,
    allow_negative_margin: bool = False,
) -> AccountingPlan:
    """Recognize retained-ownership consignment as an agency transaction.

    Instruction 291 places consignment goods without title transfer on account
    024.  Such a sale therefore uses the same net-revenue presentation as a
    commission sale; gross 702/902 accounting is reserved for stock converted
    to OWN before sale.
    """
    gross = _amount(gross_amount, label="Gross amount")
    partner = _amount(partner_amount, label="Partner amount")
    if gross <= 0 or partner <= 0:
        raise AccountingPlanError("Gross and partner amounts must be greater than zero")
    margin = gross - partner
    if margin < 0 and not allow_negative_margin:
        raise AccountingPlanError("Partner amount above gross proceeds requires loss approval")

    deduction = min(gross, partner)
    lines = [AccountingLine("principal_proceeds_deduction", debit=deduction)]
    if margin > 0:
        lines.extend(
            (
                AccountingLine("gross_sales_reclassification", debit=margin),
                AccountingLine("agency_service_revenue", credit=margin),
            )
        )
    elif margin < 0:
        lines.append(AccountingLine("agency_loss", debit=-margin))
    lines.append(AccountingLine("unreported_consignment_liability", credit=partner))
    return _validate_plan(AccountingPlan("CONSIGNMENT_RECOGNITION", tuple(lines)))


def build_settlement_debt(
    partner_amount: Decimal | str | int | float,
    *,
    relationship_model: Literal["COMMISSION", "CONSIGNMENT"],
    supplier: str,
    report_doctype: str,
    report_name: str,
) -> AccountingPlan:
    """Move an unreported balance to the official Supplier payable."""
    partner = _amount(partner_amount, label="Partner amount")
    if partner <= 0:
        raise AccountingPlanError("Partner amount must be greater than zero")
    if not supplier or not report_doctype or not report_name:
        raise AccountingPlanError("Supplier and report reference are required")

    source_key = {
        "COMMISSION": "unreported_commission_liability",
        "CONSIGNMENT": "unreported_consignment_liability",
    }[relationship_model]
    return _validate_plan(
        AccountingPlan(
            "SETTLEMENT_DEBT",
            (
                AccountingLine(source_key, debit=partner),
                AccountingLine(
                    "supplier_payable",
                    credit=partner,
                    party_type="Supplier",
                    party=supplier,
                    reference_doctype=report_doctype,
                    reference_name=report_name,
                ),
            ),
        )
    )


def validate_payment_report_binding(report_name: str | None, referenced_reports: list[str]) -> str:
    """Require one PE to point to exactly one third-party settlement report."""
    unique_reports = {name for name in referenced_reports if name}
    if not report_name:
        raise AccountingPlanError("Payment Entry must link a settlement report")
    if unique_reports != {report_name}:
        raise AccountingPlanError(
            "Payment Entry references must belong to exactly its linked settlement report"
        )
    return report_name


def calculate_currency_outstanding(
    report_total: Decimal | str | int | float,
    *,
    positive_revision_delta: Decimal | str | int | float = 0,
    negative_revision_delta: Decimal | str | int | float = 0,
    payments: Decimal | str | int | float = 0,
    writeoffs: Decimal | str | int | float = 0,
) -> Decimal:
    """Calculate the source-of-truth outstanding in obligation currency."""
    outstanding = (
        _amount(report_total, label="Report total")
        + _amount(positive_revision_delta, label="Positive revision delta")
        - _amount(negative_revision_delta, label="Negative revision delta")
        - _amount(payments, label="Payments")
        - _amount(writeoffs, label="Write-offs")
    )
    if outstanding < 0:
        raise AccountingPlanError("Payment exceeds the report balance without approved overpayment")
    return outstanding


def calculate_payment_base_amount(
    obligation_amount: Decimal | str | int | float,
    payment_exchange_rate: Decimal | str | int | float,
) -> Decimal:
    amount = _amount(obligation_amount, label="Obligation amount")
    rate = _amount(payment_exchange_rate, label="Payment exchange rate")
    if amount <= 0 or rate <= 0:
        raise AccountingPlanError("Payment amount and exchange rate must be greater than zero")
    return amount * rate


def calculate_exchange_difference(
    obligation_amount: Decimal | str | int | float,
    provisional_exchange_rate: Decimal | str | int | float,
    payment_exchange_rate: Decimal | str | int | float,
) -> Decimal:
    amount = _amount(obligation_amount, label="Obligation amount")
    provisional_rate = _amount(provisional_exchange_rate, label="Provisional exchange rate")
    payment_rate = _amount(payment_exchange_rate, label="Payment exchange rate")
    if amount <= 0 or provisional_rate <= 0 or payment_rate <= 0:
        raise AccountingPlanError("Amounts and exchange rates must be greater than zero")
    return amount * (payment_rate - provisional_rate)


def resolve_adjustment_posting_date(economic_date: date, closed_through: date | None) -> date:
    """Keep the economic date but move GL posting to the first open date when needed."""
    if not closed_through or economic_date > closed_through:
        return economic_date
    return closed_through + timedelta(days=1)
