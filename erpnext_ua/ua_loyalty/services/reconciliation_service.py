from __future__ import annotations

from decimal import Decimal

import frappe

from erpnext_ua.ua_loyalty.constants import RESERVATION_ACTIVE_STATES
from erpnext_ua.ua_loyalty.context import service_write
from erpnext_ua.ua_loyalty.domain.balances import balances
from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal, money


def reconcile_account(account_name: str, *, repair: bool = False) -> dict:
    frappe.db.sql("select name from `tabUA Loyalty Account` where name=%s for update", account_name)
    account = frappe.get_doc("UA Loyalty Account", account_name)
    active = _sum("UA Loyalty Ledger Entry", "active_delta", {"account": account.name})
    pending = _sum("UA Loyalty Ledger Entry", "pending_delta", {"account": account.name})
    reserved = _sum(
        "UA Loyalty Reservation",
        "remaining_reserved_amount",
        {"account": account.name, "status": ("in", RESERVATION_ACTIVE_STATES)},
    )
    metric = _sum("UA Loyalty Metric Entry", "metric_delta", {"account": account.name})
    state = balances(active, pending, reserved)
    rebuilt = {
        "marketing_balance": state.marketing,
        "pending_balance": state.pending,
        "reserved_balance": state.reserved,
        "redeemable_balance": state.redeemable,
        "debt_balance": state.debt,
        "metric_balance": money(metric),
    }
    stored = {field: money(account.get(field)) for field in rebuilt}
    mismatches = {
        field: {"stored": str(stored[field]), "rebuilt": str(value)}
        for field, value in rebuilt.items()
        if stored[field] != value
    }
    account.reconciliation_status = "MISMATCH" if mismatches else "OK"
    account.last_reconciled_at = frappe.utils.now_datetime()
    if repair:
        for field, value in rebuilt.items():
            account.set(field, value)
        account.row_version = int(account.row_version or 0) + 1
        account.reconciliation_status = "OK"
    with service_write():
        account.save(ignore_permissions=True)
    return {
        "account": account.name,
        "status": account.reconciliation_status,
        "stored": {key: str(value) for key, value in stored.items()},
        "rebuilt": {key: str(value) for key, value in rebuilt.items()},
        "mismatches": mismatches,
        "repaired": bool(repair),
    }


def _sum(doctype: str, fieldname: str, filters: dict) -> Decimal:
    result = frappe.get_all(doctype, filters=filters, fields=[{"SUM": fieldname, "as": "total"}])
    return decimal(result[0].total if result else ZERO)
