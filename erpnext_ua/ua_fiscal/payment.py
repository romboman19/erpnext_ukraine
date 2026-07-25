from __future__ import annotations

import frappe

from erpnext_ua.ua_fiscal.payment_catalog import CHANNEL_KIND, PAYMENT_CONTEXTS, PAYMENT_FORMS


_PAYFORM_NAMES = {
	0: "ГОТІВКА",
	1: "КАРТКА",
	2: "ПЕРЕДОПЛАТА",
	3: "КРЕДИТ",
	100000: "БЕЗГОТІВКОВИЙ ПЛАТІЖНИЙ ІНСТРУМЕНТ",
}

_PAYFORM_ALIASES = {
	"cash": "ГОТІВКА",
	"готівка": "ГОТІВКА",
	"card": "КАРТКА",
	"credit card": "КАРТКА",
	"debit card": "КАРТКА",
	"картка": "КАРТКА",
	"банківська картка": "КАРТКА",
	"iban": "ПЕРЕКАЗ НА РАХУНОК",
	"bank transfer": "ПЕРЕКАЗ НА РАХУНОК",
	"bonus": "БОНУСИ",
	"installment": "РОЗСТРОЧКА",
}


def canonical_payform_name(code: int | str | None, name: str | None = None) -> str:
	"""Return a stable Ukrainian fiscal payment name.

	Codes 0/1 have protocol-wide semantics in this app. For custom codes we
	preserve a configured Ukrainian name and translate only known ERPNext/POS
	aliases, avoiding an invented meaning for a merchant-defined code.
	"""
	try:
		numeric_code = int(code) if code not in (None, "") else None
	except (TypeError, ValueError):
		numeric_code = None
	normalized = str(name or "").strip()
	if normalized:
		return _PAYFORM_ALIASES.get(normalized.casefold(), normalized)
	return _PAYFORM_NAMES.get(numeric_code, "ІНШИЙ ЗАСІБ ОПЛАТИ")


def fiscal_payform_name(kind: str | None, code: int | str | None, configured_name: str | None = None) -> str:
	"""Normalize an internal POS payment kind before it enters signed XML."""
	if str(configured_name or "").strip():
		return canonical_payform_name(code, configured_name)
	by_kind = {
		"Cash": "ГОТІВКА",
		"Card": "КАРТКА",
		"IBAN": "ПЕРЕКАЗ НА РАХУНОК",
		"Bonus": "БОНУСИ",
		"Installment": "РОЗСТРОЧКА",
	}
	return by_kind.get(str(kind or "")) or canonical_payform_name(code, configured_name)


PAYMENT_METHOD_FIELDS = [
	"name",
	"enabled",
	"ua_pos_enabled",
	"ua_pos_channel",
	"ua_prro_payment_form",
	"ua_prro_payment_means",
	"ua_payformcd",
	"ua_allow_cashless",
	"ua_allow_other",
	"ua_allow_prepayment",
	"ua_allow_debt",
	"ua_requires_terminal",
	"ua_prro_code_verified",
	"ua_currency",
]


def normalize_payment_method(row: dict, *, requested_form: str | None = None, context: str | None = None) -> dict:
	"""Validate one Mode of Payment and return an immutable POS/fiscal snapshot."""
	row = frappe._dict(row)
	if not row.enabled or not row.ua_pos_enabled:
		raise ValueError("Спосіб оплати вимкнено для UA POS")
	if not row.ua_prro_code_verified:
		raise ValueError("Код XML ДПС для способу оплати не підтверджено")
	form = str(requested_form or row.ua_prro_payment_form or "").strip().upper()
	if form not in PAYMENT_FORMS:
		raise ValueError("Не визначено форму оплати ДПС")
	if form == "ГОТІВКА" and int(row.ua_payformcd or 0) != 0:
		raise ValueError("Готівкова форма повинна мати код PAYFORMCD 0")
	if form != "ГОТІВКА" and int(row.ua_payformcd or 0) == 0:
		raise ValueError("Безготівкова або інша форма не може мати код PAYFORMCD 0")
	if form == "БЕЗГОТІВКОВА" and not row.ua_allow_cashless:
		raise ValueError("Засіб не дозволений для безготівкової форми")
	if form == "ІНШЕ" and not row.ua_allow_other:
		raise ValueError("Засіб не дозволений для форми «ІНШЕ»")
	context = str(context or "Звичайна оплата").strip()
	if context not in PAYMENT_CONTEXTS:
		raise ValueError("Невідомий контекст оплати")
	if context == "Передоплата" and not row.ua_allow_prepayment:
		raise ValueError("Засіб не дозволений для передоплати")
	if context == "Борг" and not row.ua_allow_debt:
		raise ValueError("Засіб не дозволений для погашення боргу")
	channel = str(row.ua_pos_channel or "").strip()
	if channel not in CHANNEL_KIND:
		raise ValueError("Не визначено технічний канал оплати")
	if row.ua_requires_terminal and channel != "Платіжний термінал":
		raise ValueError("Ознака термінала несумісна з технічним каналом")
	means = canonical_payform_name(row.ua_payformcd, row.ua_prro_payment_means or row.name)
	return {
		"mode_of_payment": row.name,
		"kind": CHANNEL_KIND[channel],
		"payment_form": form,
		"payment_means": means,
		"payment_code": int(row.ua_payformcd or 0),
		"payment_context": context,
		"requires_terminal": int(bool(row.ua_requires_terminal)),
		"channel": channel,
		"currency": row.ua_currency or "UAH",
	}


def configured_payment_methods() -> list[dict]:
	rows = frappe.get_all(
		"Mode of Payment",
		filters={"enabled": 1, "ua_pos_enabled": 1},
		fields=PAYMENT_METHOD_FIELDS,
		order_by="ua_prro_payment_form asc, ua_prro_payment_means asc, name asc",
		limit_page_length=200,
	)
	result = []
	for row in rows:
		try:
			result.append(normalize_payment_method(row))
		except ValueError:
			# Некоректний запис не можна показувати касиру; адміністратор бачить
			# його у стандартному довіднику Mode of Payment.
			continue
	return result


def resolve_payment_method(name: str, *, requested_form: str | None = None, context: str | None = None) -> dict:
	row = frappe.db.get_value("Mode of Payment", name, PAYMENT_METHOD_FIELDS[1:], as_dict=True)
	if not row:
		frappe.throw(f"Спосіб оплати {name} не знайдено")
	row["name"] = name
	try:
		return normalize_payment_method(row, requested_form=requested_form, context=context)
	except ValueError as exc:
		frappe.throw(f"Спосіб оплати {name}: {exc}")
