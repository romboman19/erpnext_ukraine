from __future__ import annotations

import requests
import frappe

from ukrainian_integrations.customer_identification.service import (
	_expire_if_needed,
	_mark_verified,
	_settings,
	normalize_phone,
)


def _telegram(method: str, payload: dict):
	settings = _settings()
	token = settings.get_password("telegram_bot_token", raise_exception=False)
	if not token:
		return None
	response = requests.post(
		f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=20
	)
	response.raise_for_status()
	return response.json()


def _validate_secret():
	settings = _settings()
	expected = settings.get_password("telegram_webhook_secret", raise_exception=False) or ""
	if expected and frappe.get_request_header("X-Telegram-Bot-Api-Secret-Token") != expected:
		frappe.throw("Unauthorized Telegram webhook", frappe.PermissionError)


@frappe.whitelist(allow_guest=True)
def webhook():
	_validate_secret()
	update = frappe.request.get_json(silent=True) or {}
	message = update.get("message") or {}
	chat = message.get("chat") or {}
	chat_id = str(chat.get("id") or "")
	text = (message.get("text") or "").strip()
	contact = message.get("contact") or {}

	if text.startswith("/start cid_"):
		token = text.split("cid_", 1)[1].split()[0]
		name = frappe.db.get_value(
			"Customer Identification Request", {"request_token": token, "status": "Pending"}, "name"
		)
		if not name:
			_telegram("sendMessage", {"chat_id": chat_id, "text": "Запит не знайдено або він уже завершений."})
			return {"ok": True}
		doc = _expire_if_needed(frappe.get_doc("Customer Identification Request", name))
		if doc.status != "Pending":
			return {"ok": True}
		doc.external_reference = chat_id
		doc.save(ignore_permissions=True)
		_telegram(
			"sendMessage",
			{
				"chat_id": chat_id,
				"text": "Для підтвердження покупця надішліть свій номер телефону.",
				"reply_markup": {
					"keyboard": [[{"text": "Надіслати мій контакт", "request_contact": True}]],
					"resize_keyboard": True,
					"one_time_keyboard": True,
				},
			},
		)
		return {"ok": True}

	if contact and chat_id:
		name = frappe.db.get_value(
			"Customer Identification Request",
			{"channel": "Telegram", "external_reference": chat_id, "status": "Pending"},
			"name",
		)
		if not name:
			return {"ok": True}
		doc = _expire_if_needed(frappe.get_doc("Customer Identification Request", name))
		phone = normalize_phone(contact.get("phone_number") or "")
		from_user = str((message.get("from") or {}).get("id") or "")
		contact_user = str(contact.get("user_id") or "")
		if phone != doc.phone or (contact_user and from_user != contact_user):
			_telegram("sendMessage", {"chat_id": chat_id, "text": "Номер контакту не відповідає запиту."})
			return {"ok": True}
		_mark_verified(doc, external_reference=f"Telegram chat:{chat_id}", telegram_user_id=from_user)
		frappe.db.commit()
		_telegram(
			"sendMessage",
			{"chat_id": chat_id, "text": "Покупця підтверджено. Можна повернутися до каси.", "reply_markup": {"remove_keyboard": True}},
		)
	return {"ok": True}

