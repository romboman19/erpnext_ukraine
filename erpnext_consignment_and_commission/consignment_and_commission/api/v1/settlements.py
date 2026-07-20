"""Versioned settlement-report and partner-payment commands."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import getdate

from ...integrations.payments import create_settlement_payment, submit_settlement_payment
from ...integrations.settlements import (
    cancel_settlement_report,
    create_settlement_report,
    submit_settlement_report,
)
from ...services.payment import SettlementPaymentError, SettlementPaymentRequest
from ...services.settlement import SettlementError, SettlementRequest
from ...setup.ownership_dimension import SETTLEMENT_REPORT_FIELD
from .common import MANAGER_ROLES, assert_permission, assert_roles, parse_decimal, parse_json


def _report_payload(report: Any) -> dict[str, Any]:
    return {
        "name": report.name,
        "docstatus": report.docstatus,
        "status": report.status,
        "supplier": report.supplier,
        "currency": report.currency,
        "conversion_rate": report.conversion_rate,
        "total_partner_amount": report.total_partner_amount,
        "base_total_partner_amount": report.base_total_partner_amount,
        "paid_amount": report.paid_amount,
        "outstanding_amount": report.outstanding_amount,
        "debt_journal_entry": report.debt_journal_entry,
    }


def _payment_payload(payment: Any) -> dict[str, Any]:
    return {
        "name": payment.name,
        "docstatus": payment.docstatus,
        "settlement_report": payment.get(SETTLEMENT_REPORT_FIELD),
        "supplier": payment.party,
        "paid_amount": payment.paid_amount,
        "currency": payment.paid_to_account_currency,
    }


@frappe.whitelist(methods=["POST"])
def create_report(
    *,
    idempotency_key: str,
    sale_allocations: str | list[str],
    period_from: str,
    period_to: str,
    posting_date: str,
) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    try:
        parsed = parse_json(sale_allocations, label="sale_allocations")
    except ValueError as exc:
        raise SettlementError(str(exc)) from exc
    if not isinstance(parsed, list):
        raise SettlementError("sale_allocations must be a JSON list")
    assert_permission("CC Settlement Report", "create")
    for value in parsed:
        assert_permission("CC Sale Allocation", "read", str(value))
    report = create_settlement_report(
        SettlementRequest(
            idempotency_key=idempotency_key,
            sale_allocations=tuple(str(value) for value in parsed),
            period_from=getdate(period_from),
            period_to=getdate(period_to),
            posting_date=getdate(posting_date),
        )
    )
    return _report_payload(report)


@frappe.whitelist(methods=["POST"])
def submit_report(*, settlement_report: str) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    assert_permission("CC Settlement Report", "submit", settlement_report)
    return _report_payload(submit_settlement_report(settlement_report))


@frappe.whitelist(methods=["POST"])
def cancel_report(*, settlement_report: str) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    assert_permission("CC Settlement Report", "cancel", settlement_report)
    return _report_payload(cancel_settlement_report(settlement_report))


@frappe.whitelist(methods=["POST"])
def create_payment(
    *,
    idempotency_key: str,
    settlement_report: str,
    bank_account: str,
    amount: str | int | float,
    posting_date: str,
    reference_no: str,
    exchange_rate: str | int | float = 1,
) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    assert_permission("Payment Entry", "create")
    assert_permission("CC Settlement Report", "read", settlement_report)
    assert_permission("Account", "read", bank_account)
    try:
        parsed_amount = parse_decimal(amount, label="amount")
        parsed_rate = parse_decimal(exchange_rate, label="exchange_rate")
    except ValueError as exc:
        raise SettlementPaymentError(str(exc)) from exc
    payment = create_settlement_payment(
        SettlementPaymentRequest(
            idempotency_key=idempotency_key,
            settlement_report=settlement_report,
            bank_account=bank_account,
            amount=parsed_amount,
            posting_date=getdate(posting_date),
            reference_no=reference_no,
            exchange_rate=parsed_rate,
        )
    )
    return _payment_payload(payment)


@frappe.whitelist(methods=["POST"])
def submit_payment(*, payment_entry: str) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    assert_permission("Payment Entry", "submit", payment_entry)
    return _payment_payload(submit_settlement_payment(payment_entry))


@frappe.whitelist(methods=["POST"])
def cancel_payment(*, payment_entry: str) -> dict[str, Any]:
    assert_roles(MANAGER_ROLES)
    assert_permission("Payment Entry", "cancel", payment_entry)
    payment = frappe.get_doc("Payment Entry", payment_entry)
    if not payment.get(SETTLEMENT_REPORT_FIELD):
        frappe.throw("Only a CC settlement Payment Entry can use this endpoint")
    if payment.docstatus == 1:
        payment.flags.ignore_permissions = True
        payment.cancel()
    return _payment_payload(payment)
