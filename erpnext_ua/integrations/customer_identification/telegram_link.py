from __future__ import annotations

import re

import frappe
from frappe import _


def _valid_chat_id(value) -> bool:
    return bool(re.fullmatch(r"-?\d{1,20}", str(value or "")))


def _normalize_chat_id(value) -> str | None:
    if value is None:
        return None
    chat_id = str(value).strip()
    return chat_id if _valid_chat_id(chat_id) else None


def get_link_by_chat_id(chat_id: str):
    name = frappe.db.get_value(
        "Customer Telegram Link",
        {"chat_id": chat_id},
        "name",
        order_by="creation desc",
    )
    if name:
        return frappe.get_doc("Customer Telegram Link", name)
    return None


def get_active_link_for_customer(customer: str):
    name = frappe.db.get_value(
        "Customer Telegram Link",
        {"customer": customer, "status": "Active"},
        "name",
        order_by="creation desc",
    )
    if name:
        return frappe.get_doc("Customer Telegram Link", name)
    return None


def _customer_phone(customer_doc) -> str | None:
    for field in ("mobile_no", "phone"):
        value = customer_doc.get(field)
        if value:
            return str(value).strip()
    return None


def _set_customer_telegram_status(customer: str, chat_id: str | None, status: str) -> None:
    meta = frappe.get_meta("Customer")
    values = {}
    if meta.has_field("ua_telegram_chat_id") and chat_id:
        values["ua_telegram_chat_id"] = chat_id
    if meta.has_field("ua_telegram_status"):
        values["ua_telegram_status"] = status
    if values:
        frappe.db.set_value("Customer", customer, values)


def ensure_telegram_link(
    customer: str,
    phone: str | None = None,
    chat_id: str | None = None,
    telegram_user_id: str | None = None,
    status: str = "Active",
) -> dict:
    """Idempotently create or refresh a Customer Telegram Link.

    If the customer already has a link, the existing record is updated in place
    so that chat IDs are not duplicated and historical verification_count is
    preserved.
    """
    if not frappe.db.exists("Customer", customer):
        frappe.throw(_("Customer {0} not found").format(customer))

    normalized_chat_id = _normalize_chat_id(chat_id)
    normalized_user_id = str(telegram_user_id or "").strip()[:40] or None
    normalized_phone = str(phone or "").strip()[:40] or None

    existing = get_active_link_for_customer(customer)
    if not existing:
        # Search by chat_id as well in case the customer was relinked.
        if normalized_chat_id:
            existing = get_link_by_chat_id(normalized_chat_id)
            if existing and existing.customer != customer:
                frappe.throw(
                    _(
                        "Telegram chat {0} is already linked to another customer"
                    ).format(normalized_chat_id)
                )

    if existing:
        doc = existing
        if normalized_chat_id:
            doc.chat_id = normalized_chat_id
        if normalized_user_id:
            doc.telegram_user_id = normalized_user_id
        if normalized_phone:
            doc.phone = normalized_phone
        doc.status = status
        if status == "Active":
            doc.stop_reason = ""
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc(
            {
                "doctype": "Customer Telegram Link",
                "customer": customer,
                "phone": normalized_phone,
                "chat_id": normalized_chat_id,
                "telegram_user_id": normalized_user_id,
                "status": status,
                "verification_count": 0,
            }
        ).insert(ignore_permissions=True)

    _set_customer_telegram_status(customer, normalized_chat_id, status)
    return _link_payload(doc)


def stop_link(chat_id: str, reason: str | None = None) -> dict | None:
    """Mark a Telegram link as stopped and update the customer's status."""
    doc = get_link_by_chat_id(chat_id)
    if not doc or doc.status != "Active":
        return None
    doc.status = "Stopped"
    doc.stop_reason = str(reason or "").strip()[:1000]
    doc.save(ignore_permissions=True)
    _set_customer_telegram_status(doc.customer, chat_id, "Stopped")
    return _link_payload(doc)


def record_verification(chat_id: str) -> dict | None:
    """Increment verification count and refresh the link timestamp."""
    doc = get_link_by_chat_id(chat_id)
    if not doc:
        return None
    doc.verification_count = int(doc.verification_count or 0) + 1
    doc.last_verified_at = frappe.utils.now_datetime()
    if doc.status != "Active":
        doc.status = "Active"
        doc.stop_reason = ""
    doc.save(ignore_permissions=True)
    _set_customer_telegram_status(doc.customer, chat_id, "Active")
    return _link_payload(doc)


def _link_payload(doc) -> dict:
    return {
        "name": doc.name,
        "customer": doc.customer,
        "chat_id": doc.chat_id,
        "telegram_user_id": doc.telegram_user_id,
        "phone": doc.phone,
        "status": doc.status,
        "verification_count": int(doc.verification_count or 0),
        "last_verified_at": str(doc.last_verified_at) if doc.last_verified_at else None,
        "stop_reason": doc.stop_reason,
    }


def on_customer_insert(doc, method=None) -> None:
    """Hook: create an Active Telegram Link if a migrated Customer already has one."""
    chat_id = doc.get("ua_telegram_chat_id")
    if not _valid_chat_id(chat_id):
        return
    phone = _customer_phone(doc)
    try:
        ensure_telegram_link(
            customer=doc.name,
            phone=phone,
            chat_id=chat_id,
            telegram_user_id=None,
            status="Active",
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Customer Telegram Link creation failed for {doc.name}",
        )


def get_permission_query_conditions(user: str | None = None) -> str:
    """Allow users to see only links for customers they own."""
    if not user:
        user = frappe.session.user
    if user == "Administrator" or "System Manager" in frappe.get_roles(user):
        return ""
    return (
        f"`tabCustomer Telegram Link`.customer IN "
        f"(SELECT name FROM `tabCustomer` WHERE owner = {frappe.db.escape(user)})"
    )
