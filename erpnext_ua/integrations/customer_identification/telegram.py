from __future__ import annotations

import json
import re

import frappe
import requests

from erpnext_ua.integrations.communication.telegram.profile import get_enabled_bot_profile
from erpnext_ua.integrations.customer_identification.service import (
    _expire_if_needed,
    _find_customer,
    _lock_request,
    _mark_verified,
    _settings,
    normalize_phone,
)
from erpnext_ua.integrations.customer_identification.telegram_link import (
    ensure_telegram_link,
    get_link_by_chat_id,
    record_verification,
    stop_link,
)
from erpnext_ua.integrations.utils.security import secrets_equal

MAX_TELEGRAM_RESPONSE_BYTES = 1024 * 1024
ALLOWED_METHODS = {"sendMessage", "answerCallbackQuery", "editMessageText"}


class TelegramAPIError(RuntimeError):
    def __init__(self, message: str, *, definite: bool):
        super().__init__(message)
        self.definite = definite


class _TemplateValues(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _format_template(template: str, **values) -> str:
    return template.format_map(_TemplateValues(values))


def _read_bounded(response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_TELEGRAM_RESPONSE_BYTES:
                raise TelegramAPIError(
                    "Telegram API response is too large",
                    definite=False,
                )
        except ValueError:
            pass
    chunks: list[bytes] = []
    received = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        received += len(chunk)
        if received > MAX_TELEGRAM_RESPONSE_BYTES:
            raise TelegramAPIError(
                "Telegram API response is too large",
                definite=False,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _identification_bot_profile():
    settings = _settings()
    return get_enabled_bot_profile(settings.telegram_bot_profile)


def _telegram(method: str, payload: dict):
    if method not in ALLOWED_METHODS:
        raise ValueError(f"Unsupported Telegram API method: {method}")
    profile = _identification_bot_profile()
    token = (profile.get_password("bot_token", raise_exception=False) or "").strip()
    if not token:
        raise ValueError("Telegram bot token is not configured")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
            timeout=(10, 20),
            allow_redirects=False,
            stream=True,
        )
        try:
            raw = _read_bounded(response)
        finally:
            response.close()
    except requests.RequestException:
        raise TelegramAPIError(
            "Telegram API request outcome is unknown",
            definite=False,
        ) from None

    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TelegramAPIError(
            "Telegram API returned an invalid response",
            definite=False,
        ) from None
    if not isinstance(data, dict):
        raise TelegramAPIError(
            "Telegram API returned an unexpected response",
            definite=False,
        )
    if response.status_code >= 300 or data.get("ok") is not True:
        definite = (
            400 <= response.status_code < 500
            and response.status_code not in {408, 429}
        )
        raise TelegramAPIError(
            f"Telegram API rejected the request with HTTP {response.status_code}",
            definite=definite,
        )
    return data


def _send_message(chat_id: str, text: str, *, reply_markup: dict | None = None) -> dict:
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _telegram("sendMessage", payload)


def _answer_callback(callback_query_id: str, text: str | None = None) -> dict:
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return _telegram("answerCallbackQuery", payload)


def _edit_message(
    chat_id: str,
    message_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> dict:
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _telegram("editMessageText", payload)


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


def _valid_chat_id(value) -> bool:
    return bool(re.fullmatch(r"-?\d{1,20}", str(value or "")))


def _normalize_chat_id(value) -> str | None:
    chat_id = str(value or "").strip()
    return chat_id if _valid_chat_id(chat_id) else None


def _masked_phone(phone: str) -> str:
    digits = phone.replace("+", "").replace(" ", "").replace("-", "")
    if len(digits) >= 7:
        return f"+{digits[:3]} *** ** {digits[-2:]}"
    return phone


def _handle_deep_link_start(chat_id: str, text: str) -> None:
    request_token = text.split("cid_", 1)[1].split()[0]
    if not re.fullmatch(r"[0-9a-f]{32}", request_token):
        return
    name = frappe.db.get_value(
        "Customer Identification Request",
        {
            "request_token": request_token,
            "status": "Pending",
        },
        "name",
    )
    if not name:
        _send_message(chat_id, "Запит не знайдено або він уже завершений.")
        return
    _lock_request(name)
    doc = _expire_if_needed(frappe.get_doc("Customer Identification Request", name))
    if doc.status != "Pending":
        return
    if str(doc.external_reference or "") == chat_id:
        return
    doc.external_reference = chat_id
    doc.save(ignore_permissions=True)
    _send_message(
        chat_id,
        "Для підтвердження покупця надішліть свій номер телефону.",
        reply_markup={
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
    )


def _handle_welcome_start(chat_id: str) -> None:
    settings = _settings()
    if not settings.telegram_enabled:
        return
    welcome = str(settings.telegram_welcome_template or "").strip()
    if not welcome:
        welcome = "Вітаємо! Надішліть свій контакт, щоб ми могли підтверджувати покупців на касі."
    consent = str(settings.telegram_consent_template or "").strip()
    text = welcome
    if consent:
        text = f"{text}\n\n{consent}"
    _send_message(
        chat_id,
        text,
        reply_markup={
            "keyboard": [
                [
                    {
                        "text": "Поділитися номером телефону",
                        "request_contact": True,
                    }
                ]
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        },
    )


def _handle_stop(chat_id: str) -> None:
    result = stop_link(chat_id, reason="Користувач надіслав /stop у Telegram-боті")
    if result:
        _send_message(
            chat_id,
            "Підписку на push-підтвердження зупинено. Щоб увімкнути знову, надішліть /start.",
            reply_markup={"remove_keyboard": True},
        )
    else:
        _send_message(
            chat_id,
            "У вас немає активної підписки на push-підтвердження.",
            reply_markup={"remove_keyboard": True},
        )


def _handle_status(chat_id: str) -> None:
    link = get_link_by_chat_id(chat_id)
    if not link:
        _send_message(
            chat_id,
            "Ви ще не підключені до push-підтверджень на касі. Надішліть /start.",
        )
        return
    customer_doc = frappe.get_doc("Customer", link.customer) if frappe.db.exists("Customer", link.customer) else None
    customer_name = customer_doc.customer_name if customer_doc else link.customer
    status_text = {
        "Active": "активна",
        "Stopped": "зупинена",
        "Unmatched": "очікує на прив’язку",
    }.get(link.status, link.status)
    _send_message(
        chat_id,
        f"Покупець: {customer_name}\n"
        f"Статус підписки: {status_text}\n"
        f"Підтверджень на касі: {int(link.verification_count or 0)}",
    )


def _verify_deep_link_request(chat_id: str, contact: dict, telegram_user_id: str) -> bool:
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
        return False
    _lock_request(name)
    doc = _expire_if_needed(frappe.get_doc("Customer Identification Request", name))
    try:
        phone = normalize_phone(str(contact.get("phone_number") or "")[:40])
    except Exception:
        phone = ""
    from_user = str(telegram_user_id)[:40]
    contact_user = str(contact.get("user_id") or "")[:40]
    if (
        doc.status != "Pending"
        or phone != doc.phone
        or (contact_user and from_user != contact_user)
    ):
        _send_message(chat_id, "Номер контакту не відповідає запиту.")
        return True
    _mark_verified(
        doc,
        external_reference=f"Telegram chat:{chat_id}",
        telegram_user_id=from_user,
    )
    if doc.customer:
        _refresh_customer_telegram(doc.customer, phone, chat_id, from_user)
    frappe.db.commit()
    _send_message(
        chat_id,
        "Покупця підтверджено. Можна повернутися до каси.",
        reply_markup={"remove_keyboard": True},
    )
    return True


def _refresh_customer_telegram(
    customer: str,
    phone: str,
    chat_id: str,
    telegram_user_id: str,
) -> None:
    meta = frappe.get_meta("Customer")
    values = {}
    if meta.has_field("ua_telegram_chat_id"):
        values["ua_telegram_chat_id"] = chat_id
    if meta.has_field("ua_telegram_status"):
        values["ua_telegram_status"] = "Active"
    if values:
        frappe.db.set_value("Customer", customer, values)
    try:
        ensure_telegram_link(
            customer=customer,
            phone=phone,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            status="Active",
        )
        record_verification(chat_id)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"Failed to refresh Customer Telegram Link for {customer}",
        )


def _handle_contact(chat_id: str, contact: dict, telegram_user_id: str) -> None:
    if not _valid_chat_id(chat_id):
        return
    try:
        phone = normalize_phone(str(contact.get("phone_number") or "")[:40])
    except Exception:
        _send_message(chat_id, "Не вдалося розпізнати номер телефону. Спробуйте ще раз.")
        return

    # First try to satisfy an active deep-link identification request.
    if _verify_deep_link_request(chat_id, contact, telegram_user_id):
        return

    settings = _settings()
    from_user = str(telegram_user_id)[:40]
    customer = _find_customer(phone)

    if customer:
        try:
            ensure_telegram_link(
                customer=customer,
                phone=phone,
                chat_id=chat_id,
                telegram_user_id=from_user,
                status="Active",
            )
        except frappe.ValidationError as exc:
            _send_message(
                chat_id,
                f"Не вдалося підключити номер: {exc}",
                reply_markup={"remove_keyboard": True},
            )
            return

        linked_template = str(settings.telegram_linked_template or "").strip()
        if not linked_template:
            linked_template = "Номер {phone} підключено. Тепер на касі можна підтверджувати покупки одним натисканням."
        _send_message(
            chat_id,
            _format_template(linked_template, phone=_masked_phone(phone)),
            reply_markup={"remove_keyboard": True},
        )
        if frappe.get_meta("Customer").has_field("ua_telegram_chat_id"):
            frappe.db.set_value(
                "Customer",
                customer,
                {
                    "ua_telegram_chat_id": chat_id,
                    "ua_telegram_status": "Active",
                },
            )
        frappe.db.commit()
        return

    unmatched_template = str(settings.telegram_unmatched_template or "").strip()
    if not unmatched_template:
        unmatched_template = "Ми не знайшли покупця з номером {phone}. Зверніться до касира або зареєструйтесь у магазині."
    _send_message(
        chat_id,
        _format_template(unmatched_template, phone=_masked_phone(phone)),
        reply_markup={"remove_keyboard": True},
    )


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

    # Handle inline-button callback queries (push confirmation / cancellation).
    callback_query = update.get("callback_query") or {}
    if isinstance(callback_query, dict) and callback_query.get("id"):
        _handle_callback_query(callback_query)
        return {"ok": True}

    message = update.get("message") or {}
    if not isinstance(message, dict):
        return {"ok": True}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "") if isinstance(chat, dict) else ""
    text = str(message.get("text") or "").strip()[:500]
    contact = message.get("contact") or {}
    telegram_user_id = (message.get("from") or {}).get("id")

    if isinstance(contact, dict) and contact and _valid_chat_id(chat_id):
        _handle_contact(chat_id, contact, telegram_user_id)
        return {"ok": True}

    if text.startswith("/start cid_"):
        _handle_deep_link_start(chat_id, text)
        return {"ok": True}

    if text == "/start":
        _handle_welcome_start(chat_id)
        return {"ok": True}

    if text == "/stop":
        _handle_stop(chat_id)
        return {"ok": True}

    if text == "/status":
        _handle_status(chat_id)
        return {"ok": True}

    return {"ok": True}


def _handle_callback_query(callback_query: dict) -> None:
    """Placeholder for PR 4 push-confirmation callback handling.

    Deep-link and standalone commands are handled through regular messages; this
    function acks callback queries that do not match a known verification token
    so the user sees immediate feedback.
    """
    callback_query_id = str(callback_query.get("id") or "")
    if not callback_query_id:
        return
    data = str(callback_query.get("data") or "").strip()
    if not data:
        _answer_callback(callback_query_id, "Дія не розпізнана.")
        return
    # PR 4 will attach meaning to cid_... / cnc_... payloads here.
    _answer_callback(callback_query_id, "Обробляємо ваш вибір…")
