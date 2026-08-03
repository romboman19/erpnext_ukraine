from __future__ import annotations

from decimal import Decimal

import frappe

from erpnext_ua.ua_loyalty.context import service_write
from erpnext_ua.ua_loyalty.domain.balances import balances
from erpnext_ua.ua_loyalty.domain.money import decimal
from erpnext_ua.ua_loyalty.domain.tiers import Tier, select_tier
from erpnext_ua.ua_loyalty.exceptions import LoyaltyError


def account_for(customer: str, scope: str, *, create: bool = False, program: str | None = None):
    name = frappe.db.get_value("UA Loyalty Account", {"customer": customer, "scope": scope}, "name")
    if name:
        return frappe.get_doc("UA Loyalty Account", name)
    if not create:
        raise LoyaltyError("Для покупця немає рахунку в цій області", "LOYALTY_ACCOUNT_NOT_FOUND")
    program = program or frappe.db.get_value("UA Loyalty Scope", scope, "default_program")
    if not program:
        raise LoyaltyError("Для області не визначено програму", "LOYALTY_PROGRAM_NOT_FOUND")
    with service_write():
        return frappe.get_doc(
            {
                "doctype": "UA Loyalty Account",
                "customer": customer,
                "scope": scope,
                "program": program,
                "card_type": frappe.db.get_value("UA Loyalty Program", program, "default_card_type"),
                "status": "ACTIVE",
                "opt_in_at": frappe.utils.now_datetime(),
            }
        ).insert(ignore_permissions=True)


def lock_account(account: str):
    frappe.db.sql("select name from `tabUA Loyalty Account` where name=%s for update", account)
    return frappe.get_doc("UA Loyalty Account", account)


def current_tier(account, program=None):
    program = program or frappe.get_doc("UA Loyalty Program", account.program)
    tiers = tuple(
        Tier(row.tier_code, decimal(row.threshold_amount), decimal(row.earn_percent)) for row in program.tiers
    )
    return select_tier(decimal(account.metric_balance), tiers)


def update_cached_balances(
    account, *, active_delta=Decimal("0"), pending_delta=Decimal("0"), reserved_delta=Decimal("0")
):
    account.marketing_balance = decimal(account.marketing_balance) + decimal(active_delta)
    account.pending_balance = decimal(account.pending_balance) + decimal(pending_delta)
    account.reserved_balance = decimal(account.reserved_balance) + decimal(reserved_delta)
    state = balances(account.marketing_balance, account.pending_balance, account.reserved_balance)
    account.marketing_balance = state.marketing
    account.pending_balance = state.pending
    account.reserved_balance = state.reserved
    account.redeemable_balance = state.redeemable
    account.debt_balance = state.debt
    account.row_version = int(account.row_version or 0) + 1
    with service_write():
        account.save(ignore_permissions=True)
    return state


def update_metric(account, delta: Decimal):
    account.metric_balance = decimal(account.metric_balance) + decimal(delta)
    tier = current_tier(account)
    account.current_tier_code = tier.code
    account.current_percent = tier.rate
    account.row_version = int(account.row_version or 0) + 1
    with service_write():
        account.save(ignore_permissions=True)
