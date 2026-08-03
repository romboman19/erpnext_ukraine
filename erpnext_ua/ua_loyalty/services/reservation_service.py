from __future__ import annotations

import json
import uuid
from decimal import Decimal

import frappe

from erpnext_ua.ua_loyalty.constants import RESERVATION_ACTIVE_STATES
from erpnext_ua.ua_loyalty.context import service_write
from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal, money
from erpnext_ua.ua_loyalty.domain.snapshots import payload_hash
from erpnext_ua.ua_loyalty.exceptions import LoyaltyConflict, LoyaltyError

from .account_service import lock_account, update_cached_balances
from .settings import settings


def reserve(order, *, quote_hash: str, idempotency_key: str):
    frappe.db.sql("select name from `tabPOS Order` where name=%s for update", order.name)
    order.reload()
    if order.loyalty_quote_hash != quote_hash or order.loyalty_state != "QUOTED":
        raise LoyaltyError("Потрібно оновити розрахунок бонусів", "LOYALTY_REQUOTE_REQUIRED")
    quote = json.loads(order.loyalty_quote_json)
    existing = frappe.db.get_value("UA Loyalty Reservation", {"idempotency_key": idempotency_key}, "name")
    if existing:
        reservation = frappe.get_doc("UA Loyalty Reservation", existing)
        if reservation.quote_hash != quote_hash:
            raise LoyaltyConflict("Reservation idempotency conflict", "LOYALTY_IDEMPOTENCY_CONFLICT")
        return reservation
    account = lock_account(order.loyalty_account)
    if int(account.row_version or 0) != int(quote["account_row_version"]):
        raise LoyaltyError("Баланс змінився після quote", "LOYALTY_REQUOTE_REQUIRED")
    requested = money(quote["allowed_redemption"])
    if requested > money(account.redeemable_balance):
        raise LoyaltyError("Недостатньо доступних бонусів", "LOYALTY_INSUFFICIENT_REDEEMABLE")
    if requested <= ZERO:
        return None
    config = settings()
    expires_at = frappe.utils.add_to_date(
        frappe.utils.now_datetime(), seconds=int(config.reservation_ttl_seconds or 900), as_datetime=True
    )
    items = [
        {
            "source_row_name": row["source_row"],
            "item_code": row["item_code"],
            "requested_amount": row["redeemed"],
            "reserved_amount": row["redeemed"],
            "line_limit": row["amount_before_loyalty"],
            "sequence": index,
        }
        for index, row in enumerate(quote["items"], 1)
        if decimal(row["redeemed"]) > ZERO
    ]
    reservation_payload = {"order": order.name, "quote_hash": quote_hash, "amount": str(requested), "items": items}
    with service_write():
        reservation = frappe.get_doc(
            {
                "doctype": "UA Loyalty Reservation",
                "reservation_token": str(uuid.uuid4()),
                "account": account.name,
                "scope": account.scope,
                "source_doctype": "POS Order",
                "source_name": order.name,
                "customer_snapshot": order.customer,
                "card_snapshot": order.loyalty_card,
                "program_version": order.loyalty_program_version,
                "quote_hash": quote_hash,
                "requested_amount": requested,
                "reserved_amount": requested,
                "remaining_reserved_amount": requested,
                "status": "ACTIVE",
                "created_at": frappe.utils.now_datetime(),
                "expires_at": expires_at,
                "idempotency_key": idempotency_key,
                "payload_hash": payload_hash(reservation_payload),
                "items": items,
            }
        ).insert(ignore_permissions=True)
    update_cached_balances(account, reserved_delta=requested)
    order.loyalty_reservation = reservation.name
    order.loyalty_reserved_amount = requested
    order.loyalty_state = "RESERVED"
    order.save(ignore_permissions=True)
    return reservation


def mark_payment_in_progress(order, reservation=None):
    reservation = reservation or (
        frappe.get_doc("UA Loyalty Reservation", order.loyalty_reservation) if order.loyalty_reservation else None
    )
    if not reservation:
        return None
    frappe.db.sql("select name from `tabUA Loyalty Account` where name=%s for update", reservation.account)
    frappe.db.sql("select name from `tabUA Loyalty Reservation` where name=%s for update", reservation.name)
    reservation.reload()
    if reservation.status != "ACTIVE":
        raise LoyaltyError("Reservation неактивна", "LOYALTY_RESERVATION_STATE_CONFLICT")
    config = settings()
    with service_write():
        reservation.status = "PAYMENT_IN_PROGRESS"
        reservation.lease_extended_at = frappe.utils.now_datetime()
        reservation.expires_at = frappe.utils.add_to_date(
            reservation.lease_extended_at, seconds=int(config.payment_reservation_ttl_seconds or 3600), as_datetime=True
        )
        reservation.row_version = int(reservation.row_version or 0) + 1
        reservation.save(ignore_permissions=True)
    order.loyalty_state = "PAYMENT_IN_PROGRESS"
    order.save(ignore_permissions=True)
    return reservation


def release(
    reservation_name: str,
    *,
    allow_payment_in_progress: bool = False,
    reason_code: str | None = None,
    idempotency_key: str | None = None,
):
    reason_code = (reason_code or "").strip()
    idempotency_key = (idempotency_key or "").strip()
    if bool(reason_code) != bool(idempotency_key):
        raise LoyaltyError(
            "Reason та idempotency_key мають передаватися разом",
            "LOYALTY_RELEASE_IDEMPOTENCY_REQUIRED",
        )
    if idempotency_key:
        existing = frappe.db.get_value(
            "UA Loyalty Reservation", {"release_idempotency_key": idempotency_key}, "name"
        )
        if existing and existing != reservation_name:
            raise LoyaltyConflict(
                "Release idempotency key already belongs to another reservation",
                "LOYALTY_IDEMPOTENCY_CONFLICT",
            )
    reservation = frappe.get_doc("UA Loyalty Reservation", reservation_name)
    account = lock_account(reservation.account)
    frappe.db.sql("select name from `tabUA Loyalty Reservation` where name=%s for update", reservation.name)
    reservation.reload()
    if reservation.release_idempotency_key and (
        reservation.release_idempotency_key != idempotency_key
        or reservation.release_reason_code != reason_code
    ):
        raise LoyaltyConflict("Release payload conflict", "LOYALTY_IDEMPOTENCY_CONFLICT")
    if reservation.status in {"RELEASED", "EXPIRED", "CANCELLED", "CONSUMED"}:
        return reservation
    if reservation.status == "PAYMENT_IN_PROGRESS" and not allow_payment_in_progress:
        raise LoyaltyError("Стан зовнішньої оплати ще не визначено", "LOYALTY_PAYMENT_UNRESOLVED")
    remaining = money(reservation.remaining_reserved_amount)
    with service_write():
        reservation.status = "RELEASED"
        reservation.remaining_reserved_amount = ZERO
        reservation.release_reason_code = reason_code or None
        reservation.release_idempotency_key = idempotency_key or None
        reservation.row_version = int(reservation.row_version or 0) + 1
        reservation.save(ignore_permissions=True)
    update_cached_balances(account, reserved_delta=-remaining)
    return reservation


def consume(reservation_name: str, amount: Decimal):
    reservation = frappe.get_doc("UA Loyalty Reservation", reservation_name)
    account = lock_account(reservation.account)
    frappe.db.sql("select name from `tabUA Loyalty Reservation` where name=%s for update", reservation.name)
    reservation.reload()
    if reservation.status not in RESERVATION_ACTIVE_STATES:
        raise LoyaltyError("Reservation не може бути спожита", "LOYALTY_RESERVATION_STATE_CONFLICT")
    amount = money(amount)
    remaining = money(reservation.remaining_reserved_amount)
    if amount > remaining:
        raise LoyaltyError("Сума перевищує reservation", "LOYALTY_RESERVATION_STATE_CONFLICT")
    with service_write():
        reservation.consumed_amount = money(reservation.consumed_amount) + amount
        reservation.remaining_reserved_amount = remaining - amount
        reservation.status = "CONSUMED" if reservation.remaining_reserved_amount == ZERO else "PARTIALLY_CONSUMED"
        reservation.row_version = int(reservation.row_version or 0) + 1
        reservation.save(ignore_permissions=True)
    update_cached_balances(account, reserved_delta=-amount)
    return reservation
