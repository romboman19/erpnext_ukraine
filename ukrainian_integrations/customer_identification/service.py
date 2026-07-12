from __future__ import annotations

import hashlib
import secrets
import uuid

import frappe
from frappe import _

from ukrainian_integrations.pbx_sms.sms.turbosms import _send_sms_internal


CHANNELS = {"SMS": "sms_enabled", "Telegram": "telegram_enabled", "Call": "call_enabled"}
FINAL_STATUSES = {"Verified", "Expired", "Failed", "Cancelled"}


def normalize_phone(phone: str) -> str:
	digits = "".join(ch for ch in (phone or "") if ch.isdigit())
	if len(digits) == 10 and digits.startswith("0"):
		digits = "38" + digits
	if len(digits) == 12 and digits.startswith("380"):
		return f"+{digits}"
	frappe.throw(_("Вкажіть український номер у форматі +380XXXXXXXXX"))


def _settings():
	return frappe.get_cached_doc("Customer Identification Settings")


def _hash_code(request_token: str, code: str) -> str:
	return hashlib.sha256(f"{request_token}:{code}".encode()).hexdigest()


def _is_expired(doc) -> bool:
	return bool(doc.expires_at and frappe.utils.now_datetime() > frappe.utils.get_datetime(doc.expires_at))


def _expire_if_needed(doc):
	if doc.status == "Pending" and _is_expired(doc):
		doc.status = "Expired"
		doc.save(ignore_permissions=True)
	return doc


def _find_customer(phone: str) -> str | None:
	digits = phone.replace("+", "")
	for field in ("mobile_no", "phone"):
		for variant in (phone, digits, digits[-10:]):
			customer = frappe.db.get_value("Customer", {field: ("like", f"%{variant}%")}, "name")
			if customer:
				return customer
	return None


def _customer_payload(customer: str | None) -> dict | None:
	if not customer:
		return None
	row = frappe.db.get_value(
		"Customer", customer, ["name", "customer_name", "mobile_no", "phone"], as_dict=True
	)
	return dict(row) if row else None


def _rate_limit(phone: str, settings) -> None:
	window_start = frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-10)
	count = frappe.db.count(
		"Customer Identification Request",
		filters={"phone": phone, "creation": (">=", window_start)},
	)
	if count >= int(settings.rate_limit_per_10_min or 5):
		frappe.throw(_("Забагато спроб. Повторіть ідентифікацію пізніше."), frappe.RateLimitExceededError)


def _public_result(doc, *, debug_code: str | None = None) -> dict:
	settings = _settings()
	result = {
		"request_id": doc.name,
		"request_token": doc.request_token,
		"channel": doc.channel,
		"phone": doc.phone,
		"status": doc.status,
		"expires_at": str(doc.expires_at),
		"customer": _customer_payload(doc.customer),
		"instructions": doc.instructions or "",
	}
	if doc.channel == "Telegram" and settings.telegram_bot_username:
		result["deep_link"] = f"https://t.me/{settings.telegram_bot_username}?start=cid_{doc.request_token}"
	if debug_code and settings.test_mode and "System Manager" in frappe.get_roles():
		result["debug_code"] = debug_code
	return result


def _channel_enabled(channel: str, settings) -> bool:
	return channel in CHANNELS and bool(settings.get(CHANNELS[channel]))


@frappe.whitelist()
def get_config() -> dict:
	settings = _settings()
	channels = [channel for channel in CHANNELS if _channel_enabled(channel, settings)]
	return {
		"enabled": bool(settings.enabled),
		"channels": channels,
		"ttl_minutes": int(settings.ttl_minutes or 5),
		"call_verification_number": settings.call_verification_number or "",
		"telegram_bot_username": settings.telegram_bot_username or "",
		"test_mode": bool(settings.test_mode),
	}


@frappe.whitelist()
def begin(channel: str, phone: str, reference_doctype: str | None = None, reference_name: str | None = None) -> dict:
	settings = _settings()
	if not settings.enabled:
		frappe.throw(_("Модуль ідентифікації покупця вимкнений"))
	channel = (channel or "").strip()
	if not _channel_enabled(channel, settings):
		frappe.throw(_("Канал {0} не налаштований").format(channel))
	phone = normalize_phone(phone)
	_rate_limit(phone, settings)

	request_token = uuid.uuid4().hex
	code = f"{secrets.randbelow(1_000_000):06d}"
	expires_at = frappe.utils.add_to_date(
		frappe.utils.now_datetime(), minutes=int(settings.ttl_minutes or 5), as_datetime=True
	)
	instructions = {
		"SMS": _("Введіть шестизначний код із SMS."),
		"Telegram": _("Відкрийте Telegram-бота та надішліть свій контакт."),
		"Call": _("Зателефонуйте з цього номера на {0}.").format(settings.call_verification_number or "номер магазину"),
	}[channel]
	doc = frappe.get_doc(
		{
			"doctype": "Customer Identification Request",
			"request_token": request_token,
			"channel": channel,
			"phone": phone,
			"status": "Pending",
			"code_hash": _hash_code(request_token, code) if channel == "SMS" else "",
			"expires_at": expires_at,
			"customer": _find_customer(phone),
			"initiated_by": frappe.session.user,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"instructions": instructions,
		}
	).insert(ignore_permissions=True)

	if channel == "SMS" and not settings.test_mode:
		text = (settings.sms_template or "Код підтвердження: {code}. Дійсний {minutes} хв.").format(
			code=code, minutes=int(settings.ttl_minutes or 5)
		)
		response = _send_sms_internal(phone, text, sender=settings.sms_sender or None)
		doc.external_reference = frappe.as_json(response.get("response") or {})[:1000]
		doc.save(ignore_permissions=True)

	frappe.db.commit()
	return _public_result(doc, debug_code=code if channel == "SMS" else None)


def _mark_verified(doc, *, external_reference: str | None = None, telegram_user_id: str | None = None):
	doc.status = "Verified"
	doc.customer = doc.customer or _find_customer(doc.phone)
	doc.verified_at = frappe.utils.now_datetime()
	doc.verified_by = frappe.session.user if frappe.session.user != "Guest" else "Administrator"
	if external_reference:
		doc.external_reference = external_reference[:1000]
	if telegram_user_id:
		doc.telegram_user_id = str(telegram_user_id)
	doc.save(ignore_permissions=True)
	frappe.publish_realtime(
		"customer_identification_verified",
		{"request_id": doc.name, "customer": doc.customer, "phone": doc.phone},
		user=doc.initiated_by,
		after_commit=True,
	)
	return doc


@frappe.whitelist()
def confirm(request_id: str, code: str | None = None) -> dict:
	doc = _expire_if_needed(frappe.get_doc("Customer Identification Request", request_id))
	if doc.status in FINAL_STATUSES:
		return _public_result(doc)
	if doc.channel != "SMS":
		return _public_result(doc)
	if int(doc.attempts or 0) >= int(_settings().max_attempts or 5):
		doc.status = "Failed"
		doc.save(ignore_permissions=True)
		frappe.throw(_("Ліміт спроб вичерпано"))
	doc.attempts = int(doc.attempts or 0) + 1
	if not secrets.compare_digest(doc.code_hash or "", _hash_code(doc.request_token, (code or "").strip())):
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.throw(_("Неправильний код підтвердження"))
	_mark_verified(doc)
	frappe.db.commit()
	return _public_result(doc)


@frappe.whitelist()
def get_status(request_id: str) -> dict:
	doc = _expire_if_needed(frappe.get_doc("Customer Identification Request", request_id))
	return _public_result(doc)


@frappe.whitelist()
def cancel(request_id: str) -> dict:
	doc = frappe.get_doc("Customer Identification Request", request_id)
	if doc.status == "Pending":
		doc.status = "Cancelled"
		doc.save(ignore_permissions=True)
	return _public_result(doc)


@frappe.whitelist()
def quick_create(phone: str, customer_name: str) -> dict:
	phone = normalize_phone(phone)
	existing = _find_customer(phone)
	if existing:
		return _customer_payload(existing)
	if not (customer_name or "").strip():
		frappe.throw(_("Вкажіть ім’я покупця"))
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer_name.strip(),
			"customer_type": "Individual",
			"customer_group": frappe.db.get_single_value("Selling Settings", "customer_group") or "Individual",
			"territory": frappe.db.get_single_value("Selling Settings", "territory") or "Ukraine",
			"mobile_no": phone,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	return _customer_payload(doc.name)


def confirm_inbound_call(phone: str, call_id: str, status: str, duration: int = 0) -> str | None:
	if (status or "").lower() not in {"answered", "up", "completed", "hangup"}:
		return None
	phone = normalize_phone(phone)
	rows = frappe.get_all(
		"Customer Identification Request",
		filters={"channel": "Call", "phone": phone, "status": "Pending"},
		fields=["name"],
		order_by="creation desc",
		limit=1,
	)
	if not rows:
		return None
	doc = _expire_if_needed(frappe.get_doc("Customer Identification Request", rows[0].name))
	if doc.status != "Pending":
		return None
	_mark_verified(doc, external_reference=f"VitalPBX:{call_id};duration={duration}")
	return doc.name


def expire_pending():
	for name in frappe.get_all(
		"Customer Identification Request",
		filters={"status": "Pending", "expires_at": ("<", frappe.utils.now_datetime())},
		pluck="name",
		limit=500,
	):
		frappe.db.set_value(
			"Customer Identification Request", name, "status", "Expired", update_modified=False
		)
