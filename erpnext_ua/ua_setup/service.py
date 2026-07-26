"""Читання стану сайту та ідемпотентні кроки налаштування.

Кожен ``apply_*`` можна викликати повторно: він або створює те, чого немає, або
повертає вже наявний документ. Жоден крок не переписує дані, які адміністратор
уже змінив вручну, і жоден не робить нічого з мережею.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpnext_ua.ua_setup.readiness import (
	REQUIRED_LANGUAGE,
	Check,
	SetupState,
	Status,
	can_fiscalize,
	can_sell,
	evaluate,
)

RETAIL_CUSTOMER = "Роздрібний покупець"
DEFAULT_CASH_DESK = "Каса 1"
CONNECTOR_SETTINGS = {
	"Нова Пошта": "NP Sender Profile",
	"Укрпошта": "UP Sender Profile",
	"Rozetka Delivery": "RZ Delivery Sender Profile",
	"Monobank": "Monobank Settings",
	"PrivatBank": "PrivatBank Settings",
	"LiqPay": "LiqPay Settings",
	"TurboSMS": "TurboSMS Settings",
	"Telegram": "Telegram Bot Profile",
	"VitalPBX": "VitalPBX Settings",
	"ocStore": "OcStore Settings",
}


def collect_state(company: str | None = None) -> SetupState:
	"""Зібрати факти про сайт.

	Функція має працювати і в ``after_install``, коли частина таблиць та
	кастомних полів ще не створена, тому кожне звернення до бази захищене:
	відсутня таблиця означає «крок не виконано», а не помилку.
	"""
	company = company or _default_company()
	company_row = _company_row(company)

	return SetupState(
		company=company or "",
		company_country=company_row.get("country") or "",
		company_currency=company_row.get("default_currency") or "",
		system_language=_single_value("System Settings", "language"),
		chart_template=company_row.get("ua_chart_template") or "",
		company_has_gl_entries=bool(company and _count("GL Entry", {"company": company})),
		tax_parameter_years=frozenset(
			int(year) for year in _all("UA Tax Parameters", pluck="year") if year
		),
		current_year=getdate(nowdate()).year,
		active_fop_profiles=_count("FOP Profile", {"status": "Active"}),
		fop_has_kved=bool(_count("FOP Profile", {"status": "Active", "kved_main": ("is", "set")})),
		warehouses=_count("Warehouse", {"company": company, "is_group": 0}) if company else 0,
		retail_customers=_count("Customer"),
		active_cash_desks=_count("POS Cash Desk", {"status": "Active"}),
		pos_cash_payment_methods=_count(
			"Mode of Payment", {"ua_pos_enabled": 1, "ua_prro_payment_form": "ГОТІВКА"}
		),
		pos_cashless_payment_methods=_count(
			"Mode of Payment", {"ua_pos_enabled": 1, "ua_prro_payment_form": "БЕЗГОТІВКОВА"}
		),
		prro_registers=_count("PRRO Cash Register"),
		prro_registers_with_device_id=_count("PRRO Cash Register", {"device_id": ("is", "set")}),
		kep_keys=_count("UA KEP Key"),
		prro_signer_configured=bool(_single_value("PRRO Settings", "signservice_url")),
		print_formats=_count("Print Format", {"module": ("in", ("UA POS", "UA Price Tags"))}),
		enabled_connectors=_enabled_connectors(),
	)


def _company_row(company: str) -> dict:
	if not company or not frappe.db.table_exists("Company"):
		return {}
	fields = ["country", "default_currency"]
	# Поле додає ensure_accounting_setup, який у after_install може виконатися пізніше.
	if frappe.db.has_column("Company", "ua_chart_template"):
		fields.append("ua_chart_template")
	return frappe.db.get_value("Company", company, fields, as_dict=True) or {}


def _count(doctype: str, filters: dict | None = None) -> int:
	if not frappe.db.table_exists(doctype):
		return 0
	filters = filters or {}
	missing_column = [
		fieldname
		for fieldname in filters
		if not frappe.db.has_column(doctype, fieldname)
	]
	if missing_column:
		return 0
	return frappe.db.count(doctype, filters)


def _all(doctype: str, **kwargs):
	if not frappe.db.table_exists(doctype):
		return []
	return frappe.get_all(doctype, **kwargs)


def _single_value(doctype: str, fieldname: str) -> str:
	if not frappe.db.table_exists("Singles"):
		return ""
	return frappe.db.get_single_value(doctype, fieldname) or ""


def _default_company() -> str:
	return frappe.defaults.get_user_default("Company") or (frappe.get_all("Company", limit=1, pluck="name") or [""])[0]


def _enabled_connectors() -> tuple[str, ...]:
	enabled = []
	for label, doctype in CONNECTOR_SETTINGS.items():
		if not frappe.db.table_exists(doctype):
			continue
		if frappe.db.exists(doctype, {"enabled": 1}):
			enabled.append(label)
	return tuple(enabled)


@frappe.whitelist()
def readiness(company: str | None = None) -> dict:
	"""Звіт готовності. Read-only: нічого не створює й не змінює."""
	state = collect_state(company)
	checks = evaluate(state)
	return {
		"company": state.company,
		"can_sell": can_sell(checks),
		"can_fiscalize": can_fiscalize(checks),
		"enabled_connectors": list(state.enabled_connectors),
		"checks": [_as_dict(check) for check in checks],
	}


def _as_dict(check: Check) -> dict:
	return {
		"step": check.step,
		"title": check.title,
		"status": check.status.value,
		"severity": check.severity.value,
		"detail": check.detail,
		"fix_action": check.fix_action,
	}


@frappe.whitelist()
def apply_language() -> dict:
	frappe.only_for("System Manager")
	settings = frappe.get_single("System Settings")
	if settings.language != REQUIRED_LANGUAGE:
		settings.language = REQUIRED_LANGUAGE
		settings.save(ignore_permissions=True)
		frappe.clear_cache()
	return {"language": REQUIRED_LANGUAGE}


@frappe.whitelist()
def apply_tax_parameters() -> dict:
	frappe.only_for("System Manager")
	from erpnext_ua.install import ensure_tax_parameters

	ensure_tax_parameters()
	return {"years": sorted(int(year) for year in frappe.get_all("UA Tax Parameters", pluck="year") if year)}


@frappe.whitelist()
def apply_payment_methods() -> dict:
	frappe.only_for("System Manager")
	from erpnext_ua.install import ensure_payment_method_catalog

	ensure_payment_method_catalog()
	return {"enabled": frappe.db.count("Mode of Payment", {"ua_pos_enabled": 1})}


@frappe.whitelist()
def apply_chart(company: str, chart_template: str) -> dict:
	"""Обгортка над UA Chart of Accounts Setup, а не власна реалізація.

	Підтвердженням для сервісу заміни плану є точна назва компанії; майстер не
	винаходить слабшу перевірку, він лише передає її з боку сервера.
	"""
	from erpnext_ua.ua_accounting.chart_setup import apply_chart as apply_company_chart

	return apply_company_chart(company, chart_template, company)


@frappe.whitelist()
def chart_preflight(company: str, chart_template: str) -> dict:
	from erpnext_ua.ua_accounting.chart_setup import preflight

	return preflight(company, chart_template)


@frappe.whitelist()
def apply_fop_profile(
	company: str,
	fop_full_name: str,
	prro_registered_name: str,
	tax_id: str,
	single_tax_group: str,
	tax_rate_mode: str,
	kved_main: str | None = None,
	registration_address: str | None = None,
	iban: str | None = None,
	vat_payer: int = 0,
	vat_number: str | None = None,
) -> dict:
	frappe.only_for("System Manager")
	existing = frappe.db.exists("FOP Profile", {"company": company, "tax_id": tax_id})
	if existing:
		return {"fop_profile": existing, "created": False}

	profile = frappe.get_doc(
		{
			"doctype": "FOP Profile",
			"company": company,
			"fop_full_name": fop_full_name,
			"prro_registered_name": prro_registered_name,
			"tax_id": tax_id,
			"single_tax_group": single_tax_group,
			"tax_rate_mode": tax_rate_mode,
			"status": "Active",
			"kved_main": kved_main or None,
			"registration_address": registration_address or None,
			"iban": iban or None,
			"vat_payer": int(vat_payer or 0),
			"vat_number": vat_number or None,
		}
	).insert()
	return {"fop_profile": profile.name, "created": True}


@frappe.whitelist()
def apply_cash_desk(company: str, warehouse: str | None = None, desk_name: str | None = None) -> dict:
	"""Створює роздрібного покупця й касу, якщо їх ще немає."""
	frappe.only_for("System Manager")
	customer = _ensure_retail_customer()
	warehouse = warehouse or _first_warehouse(company)
	if not warehouse:
		frappe.throw(_("Для компанії {0} немає складу: створіть його перед касою").format(company))

	desk_name = desk_name or DEFAULT_CASH_DESK
	existing = frappe.db.exists("POS Cash Desk", {"company": company, "desk_name": desk_name})
	if existing:
		return {"cash_desk": existing, "customer": customer, "created": False}

	desk = frappe.get_doc(
		{
			"doctype": "POS Cash Desk",
			"desk_name": desk_name,
			"status": "Active",
			"company": company,
			"warehouse": warehouse,
			"default_customer": customer,
		}
	).insert()
	return {"cash_desk": desk.name, "customer": customer, "created": True}


def _ensure_retail_customer() -> str:
	existing = frappe.db.exists("Customer", RETAIL_CUSTOMER)
	if existing:
		return existing
	group = (frappe.get_all("Customer Group", filters={"is_group": 0}, limit=1, pluck="name") or [None])[0]
	territory = (frappe.get_all("Territory", filters={"is_group": 0}, limit=1, pluck="name") or [None])[0]
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": RETAIL_CUSTOMER,
			"customer_type": "Individual",
			"customer_group": group,
			"territory": territory,
		}
	).insert()
	return customer.name


def _first_warehouse(company: str) -> str | None:
	warehouses = frappe.get_all(
		"Warehouse",
		filters={"company": company, "is_group": 0, "disabled": 0},
		limit=1,
		pluck="name",
	)
	return warehouses[0] if warehouses else None


@frappe.whitelist()
def apply_prro_register(
	fop_profile: str,
	register_name: str,
	fiscal_number: str,
	register_local_number: str,
	unit_name: str,
	unit_address: str,
) -> dict:
	"""Реєструє касу ПРРО локально. Жодного звернення до ДПС тут не відбувається."""
	frappe.only_for("System Manager")
	existing = frappe.db.exists("PRRO Cash Register", {"fiscal_number": fiscal_number})
	if existing:
		return {"prro_cash_register": existing, "created": False}

	register = frappe.get_doc(
		{
			"doctype": "PRRO Cash Register",
			"register_name": register_name,
			"fop_profile": fop_profile,
			"fiscal_number": fiscal_number,
			"register_local_number": register_local_number,
			"unit_name": unit_name,
			"unit_address": unit_address,
		}
	).insert()

	from erpnext_ua.install import ensure_prro_setup

	ensure_prro_setup()
	return {"prro_cash_register": register.name, "created": True}


def pending_steps(company: str | None = None) -> list[str]:
	"""Кроки, які ще не закриті — для повідомлень і тестів."""
	return [check.step for check in evaluate(collect_state(company)) if check.status is not Status.DONE]


def report_readiness() -> None:
	"""Друкує стан налаштування у вивід ``bench install-app``/``migrate``.

	Хук нічого не змінює: після встановлення адміністратор одразу бачить, що вже
	готове й що лишилося зробити в майстрі, замість того щоб шукати це самому.
	"""
	checks = evaluate(collect_state())
	print("\nERPNext Україна — готовність до роботи:")
	for check in checks:
		mark = {Status.DONE: "OK  ", Status.PENDING: "  → ", Status.BLOCKED: "  ! "}[check.status]
		detail = f" — {check.detail}" if check.detail and check.status is not Status.DONE else ""
		print(f"  {mark}{check.title}{detail}")

	if can_fiscalize(checks):
		print("Готово, включно з фіскальними чеками.\n")
		return
	if can_sell(checks):
		print("Продажі можливі; для фіскальних чеків залиште кроки ПРРО.\n")
		return
	print("Відкрийте UA Setup Wizard і закрийте обов'язкові кроки.\n")
