from __future__ import annotations

import re

import frappe
import requests

from ukrainian_integrations.communication.telegram.client import TelegramAPIError, TelegramClient
from ukrainian_integrations.customer_identification.service import (
    _expire_if_needed,
    _lock_request,
    _mark_verified,
    _settings,
    normalize_phone,
)
from ukrainian_integrations.utils.security import secrets_equal

__all__ = ["TelegramAPIError"]


def _telegram(method: str, payload: dict):
    if method not in {"sendMessage"}:
        raise ValueError("Unsupported Telegram API method")
    settings = _settings()
    token = (
        settings.get_password("telegram_bot_token", raise_exception=False) or ""
    ).strip()
    if not token:
        raise ValueError("Telegram bot token is not configured")
    return TelegramClient(token, post=requests.post).request(method, payload)


def _validate_secret() -> None:
    settings = _settings()
    expected = (
        settings.get_password(
            "telegram_webhook_secret",
            raise_exception=False,
        )
        or ""
    ).strip()
    if not expected:
        frappe.throw(
            "Telegram webhook secret is not configured",
            frappe.PermissionError,
        )
    supplied = (
        frappe.get_request_header("X-Telegram-Bot-Api-Secret-Token") or ""
    ).strip()
    if not secrets_equal(supplied, expected):
        frappe.throw("Unauthorized Telegram webhook", frappe.PermissionError)


def _valid_chat_id(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d{1,20}", value))


@frappe.whitelist(allow_guest=True)
def webhook():
    _validate_secret()
    content_length = getattr(frappe.request, "content_length", 0) or 0
    if int(content_length) > 65_536:
        frappe.local.response["http_status_code"] = 413
        return {"ok": False, "error": "payload_too_large"}
    update = frappe.request.get_json(silent=True) or {}
    if not isinstance(update, dict):
        frappe.local.response["http_status_code"] = 400
        return {"ok": False, "error": "invalid_json_object"}

    message = update.get("message") or {}
    if not isinstance(message, dict):
        return {"ok": True}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "") if isinstance(chat, dict) else ""
    text = str(message.get("text") or "").strip()[:500]
    contact = message.get("contact") or {}

    if text.startswith("/start cid_"):
        if not _valid_chat_id(chat_id):
            return {"ok": True}
        request_token = text.split("cid_", 1)[1].split()[0]
        if not re.fullmatch(r"[0-9a-f]{32}", request_token):
            return {"ok": True}
        name = frappe.db.get_value(
            "Customer Identification Request",
            {
                "request_token": request_token,
                "status": "Pending",
            },
            "name",
        )
        if not name:
            _telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Запит не знайдено або він уже завершений.",
                },
            )
            return {"ok": True}
        _lock_request(name)
        doc = _expire_if_needed(
            frappe.get_doc("Customer Identification Request", name)
        )
        if doc.status != "Pending":
            return {"ok": True}
        if str(doc.external_reference or "") == chat_id:
            return {"ok": True}
        doc.external_reference = chat_id
        doc.save(ignore_permissions=True)
        _telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Для підтвердження покупця надішліть свій номер телефону.",
                "reply_markup": {
                    "keyboard": [
                        [
                            {
                                "text": "Надіслати мій контакт",
                                "request_contact": True,
                            }
                        ]
                    ],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            },
        )
        return {"ok": True}

    if isinstance(contact, dict) and contact and _valid_chat_id(chat_id):
        name = frappe.db.get_value(
            "Customer Identification Request",
            {
                "channel": "Telegram",
                "external_reference": chat_id,
                "status": "Pending",
            },
            "name",
        )
        if not name:
            return {"ok": True}
        _lock_request(name)
        doc = _expire_if_needed(
            frappe.get_doc("Customer Identification Request", name)
        )
        try:
            phone = normalize_phone(str(contact.get("phone_number") or "")[:40])
        except Exception:
            phone = ""
        from_user = str((message.get("from") or {}).get("id") or "")[:40]
        contact_user = str(contact.get("user_id") or "")[:40]
        if (
            doc.status != "Pending"
            or phone != doc.phone
            or (contact_user and from_user != contact_user)
        ):
            _telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": "Номер контакту не відповідає запиту.",
                },
            )
            return {"ok": True}
        _mark_verified(
            doc,
            external_reference=f"Telegram chat:{chat_id}",
            telegram_user_id=from_user,
        )
        if (
            doc.customer
            and frappe.get_meta("Customer").has_field("ua_telegram_chat_id")
        ):
            frappe.db.set_value(
                "Customer",
                doc.customer,
                "ua_telegram_chat_id",
                chat_id,
            )
        frappe.db.commit()
        _telegram(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Покупця підтверджено. Можна повернутися до каси.",
                "reply_markup": {"remove_keyboard": True},
            },
        )
    return {"ok": True}
