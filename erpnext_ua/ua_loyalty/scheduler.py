from __future__ import annotations

import frappe

from erpnext_ua.ua_loyalty.context import service_write
from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal, money

from .services.account_service import lock_account
from .services.ledger_service import append_allocation, append_ledger
from .services.reservation_service import release
from .services.settings import settings


def activate_pending():
    if not settings().enabled:
        return
    limit = int(settings().activation_batch_size or 500)
    lots = frappe.get_all(
        "UA Loyalty Bonus Lot",
        filters={"status": "PENDING", "effective_datetime": ("<=", frappe.utils.now_datetime())},
        pluck="name",
        order_by="effective_datetime asc, name asc",
        limit=limit,
    )
    for name in lots:
        try:
            _activate_lot(name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"UA Loyalty activate {name}")


def _activate_lot(name: str):
    lot = frappe.get_doc("UA Loyalty Bonus Lot", name)
    account = lock_account(lot.account)
    frappe.db.sql("select name from `tabUA Loyalty Bonus Lot` where name=%s for update", lot.name)
    lot.reload()
    if lot.status != "PENDING":
        return
    reversals = frappe.get_all(
        "UA Loyalty Ledger Entry", filters={"original_entry": lot.source_ledger_entry}, fields=["pending_delta"]
    )
    remaining = money(decimal(lot.pending_amount) + sum((decimal(row.pending_delta) for row in reversals), ZERO))
    if remaining > ZERO:
        append_ledger(
            account,
            entry_type="EARN_ACTIVATE",
            active_delta=remaining,
            pending_delta=-remaining,
            idempotency_key=f"activate:{lot.name}",
            source_doctype="UA Loyalty Bonus Lot",
            source_name=lot.name,
            values={
                "original_entry": lot.source_ledger_entry,
                "effective_datetime": frappe.utils.now_datetime(),
                "expires_at": lot.expires_at,
                "expiry_writeoff_mode": frappe.db.get_value(
                    "UA Loyalty Program", account.program, "expiry_writeoff_mode"
                ),
                "lot_kind": lot.lot_kind,
            },
        )
    with service_write():
        lot.reversed_amount = money(decimal(lot.pending_amount) - remaining)
        lot.pending_amount = ZERO
        lot.status = "DEPLETED" if remaining > ZERO else "REVERSED"
        lot.row_version = int(lot.row_version or 0) + 1
        lot.save(ignore_permissions=True)


def expire_obligations():
    if not settings().enabled:
        return
    limit = int(settings().expiry_batch_size or 500)
    obligations = frappe.get_all(
        "UA Loyalty Expiry Obligation",
        filters={
            "status": ("in", ("SCHEDULED", "PARTIALLY_PROCESSED")),
            "due_datetime": ("<=", frappe.utils.now_datetime()),
        },
        pluck="name",
        order_by="due_datetime asc, name asc",
        limit=limit,
    )
    for name in obligations:
        _expire(name)


def _expire(name: str):
    obligation = frappe.get_doc("UA Loyalty Expiry Obligation", name)
    account = lock_account(obligation.account)
    frappe.db.sql("select name from `tabUA Loyalty Expiry Obligation` where name=%s for update", name)
    obligation.reload()
    if obligation.status not in {"SCHEDULED", "PARTIALLY_PROCESSED"}:
        return
    due = money(decimal(obligation.nominal_amount) - decimal(obligation.processed_amount))
    cap = max(ZERO, money(account.marketing_balance))
    if obligation.writeoff_mode == "LOT_REMAINING" and obligation.source_bonus_lot:
        lot_available = money(
            frappe.db.get_value("UA Loyalty Bonus Lot", obligation.source_bonus_lot, "available_amount") or ZERO
        )
        cap = min(cap, lot_available)
    writeoff = min(due, cap)
    if writeoff > ZERO:
        entry = append_ledger(
            account,
            entry_type="EXPIRE",
            active_delta=-writeoff,
            idempotency_key=f"expire:{obligation.name}:{obligation.processed_amount}",
            source_doctype="UA Loyalty Expiry Obligation",
            source_name=obligation.name,
            values={"original_entry": obligation.source_ledger_entry},
        )
        append_allocation(
            account=account.name,
            ledger_entry=entry.name,
            allocation_type="EXPIRY",
            bonus_amount=writeoff,
            idempotency_key=f"{entry.idempotency_key}:allocation",
            source_doctype="UA Loyalty Expiry Obligation",
            source_name=obligation.name,
            bonus_lot=obligation.source_bonus_lot,
        )
    with service_write():
        obligation.processed_amount = money(decimal(obligation.processed_amount) + writeoff)
        obligation.status = (
            "PROCESSED" if obligation.processed_amount >= obligation.nominal_amount else "SKIPPED_NO_BALANCE"
        )
        obligation.last_run_at = frappe.utils.now_datetime()
        obligation.last_run_key = f"expire:{obligation.name}:{obligation.processed_amount}"
        obligation.save(ignore_permissions=True)


def release_stale_reservations():
    if not settings().enabled:
        return
    names = frappe.get_all(
        "UA Loyalty Reservation",
        filters={"status": "ACTIVE", "expires_at": ("<", frappe.utils.now_datetime())},
        pluck="name",
        limit=500,
    )
    for name in names:
        reservation = release(
            name,
            reason_code="TTL_EXPIRED",
            idempotency_key=f"reservation-expiry:{name}",
        )
        with service_write():
            reservation.status = "EXPIRED"
            reservation.save(ignore_permissions=True)
