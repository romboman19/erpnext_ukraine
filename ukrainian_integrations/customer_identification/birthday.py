from __future__ import annotations

from datetime import date

import frappe
from frappe import _

from ukrainian_integrations.customer_identification.telegram import _telegram
from ukrainian_integrations.pbx_sms.sms.turbosms import _send_sms_internal


class _TemplateValues(dict):
	def __missing__(self, key):
		return "{" + key + "}"


def _birthday_for_year(birth_date, year: int) -> date:
	birth_date = frappe.utils.getdate(birth_date)
	try:
		return birth_date.replace(year=year)
	except ValueError:
		return date(year, 2, 28)


def _eligible(settings, customer, target_birthday: date) -> bool:
	if settings.eligible_customer_group and customer.customer_group != settings.eligible_customer_group:
		return False
	age = target_birthday.year - frappe.utils.getdate(customer.ua_date_of_birth).year
	if int(settings.min_age or 0) and age < int(settings.min_age):
		return False
	if int(settings.max_age or 0) and age > int(settings.max_age):
		return False
	return True


def _channel(settings, customer) -> str:
	configured = settings.greeting_channel or "Auto"
	if configured == "Auto":
		return "Telegram" if customer.ua_telegram_chat_id else "SMS"
	return configured


def _message(settings, customer, birthday: date) -> str:
	valid_until = frappe.utils.add_days(birthday, int(settings.days_after or 0))
	values = _TemplateValues(
		first_name=customer.ua_first_name or customer.customer_name,
		customer_name=customer.customer_name,
		discount_percent=frappe.utils.flt(settings.discount_percent),
		birthday=frappe.format_value(birthday, {"fieldtype": "Date"}),
		valid_until=frappe.format_value(valid_until, {"fieldtype": "Date"}),
	)
	return (settings.greeting_template or _("Вітаємо з днем народження!")).format_map(values)


def _log(customer: str, year: int, channel: str):
	unique_key = f"{customer}:{year}:{channel}"
	name = frappe.db.get_value("Customer Birthday Greeting Log", {"unique_key": unique_key}, "name")
	if name:
		return frappe.get_doc("Customer Birthday Greeting Log", name)
	return frappe.get_doc(
		{
			"doctype": "Customer Birthday Greeting Log",
			"customer": customer,
			"greeting_year": year,
			"channel": channel,
			"status": "Pending",
			"unique_key": unique_key,
		}
	).insert(ignore_permissions=True)


def send_scheduled_greetings() -> dict:
	if not frappe.db.exists("DocType", "POS Birthday Settings") or not frappe.db.exists(
		"DocType", "Customer Birthday Greeting Log"
	):
		return {"sent": 0, "failed": 0, "skipped": 0}
	settings = frappe.get_single("POS Birthday Settings")
	if not settings.greeting_enabled or not settings.auto_send_greetings:
		return {"sent": 0, "failed": 0, "skipped": 0}
	meta = frappe.get_meta("Customer")
	required = {"ua_date_of_birth", "ua_first_name", "ua_telegram_chat_id"}
	if not all(meta.has_field(field) for field in required):
		return {"sent": 0, "failed": 0, "skipped": 0}

	today = frappe.utils.getdate()
	target_date = frappe.utils.add_days(today, max(0, int(settings.greeting_days_before or 0)))
	customer_fields = [
		"name",
		"customer_name",
		"customer_group",
		"ua_first_name",
		"ua_date_of_birth",
		"ua_telegram_chat_id",
	]
	customer_fields.extend(field for field in ("mobile_no", "phone") if meta.has_field(field))
	customers = frappe.get_all(
		"Customer",
		filters={"disabled": 0, "ua_date_of_birth": ("is", "set")},
		fields=customer_fields,
	)
	result = {"sent": 0, "failed": 0, "skipped": 0}
	for customer in customers:
		birthday = _birthday_for_year(customer.ua_date_of_birth, target_date.year)
		if birthday != target_date or not _eligible(settings, customer, birthday):
			continue
		channel = _channel(settings, customer)
		log = _log(customer.name, birthday.year, channel)
		if log.status == "Sent":
			result["skipped"] += 1
			continue
		try:
			text = _message(settings, customer, birthday)
			if channel == "Telegram":
				if not customer.ua_telegram_chat_id:
					frappe.throw(_("Для покупця не збережено Telegram chat ID"))
				response = _telegram("sendMessage", {"chat_id": customer.ua_telegram_chat_id, "text": text})
				if response is None:
					frappe.throw(_("Telegram-бот не налаштований"))
			else:
				phone = customer.get("mobile_no") or customer.get("phone")
				if not phone:
					frappe.throw(_("Для покупця не вказано телефон"))
				response = _send_sms_internal(phone, text)
			log.status = "Sent"
			log.sent_at = frappe.utils.now_datetime()
			log.response = frappe.as_json(response or {})[:10000]
			log.error = ""
			result["sent"] += 1
		except Exception:
			log.status = "Failed"
			log.error = frappe.get_traceback()[-2000:]
			result["failed"] += 1
		log.save(ignore_permissions=True)
		frappe.db.commit()
	return result


@frappe.whitelist()
def run_birthday_greetings() -> dict:
	frappe.only_for("System Manager")
	return send_scheduled_greetings()
