import hashlib
import re
import uuid

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# Довідкові параметри 2026 (МЗП 8647 грн, ПМ для працездатних 3028 грн)
TAX_PARAMETERS = [
	{
		"year": 2026,
		"single_tax_group": "1",
		"minimum_wage": 8647,
		"income_limit": 1_444_049,
		"single_tax_monthly": 302.80,
		"military_levy_monthly": 864.70,
		"esv_monthly": 1902.34,
	},
	{
		"year": 2026,
		"single_tax_group": "2",
		"minimum_wage": 8647,
		"income_limit": 7_211_598,
		"single_tax_monthly": 1729.40,
		"military_levy_monthly": 864.70,
		"esv_monthly": 1902.34,
	},
	{
		"year": 2026,
		"single_tax_group": "3",
		"minimum_wage": 8647,
		"income_limit": 10_091_049,
		"single_tax_percent_no_vat": 5,
		"single_tax_percent_vat": 3,
		"military_levy_percent": 1,
		"esv_monthly": 1902.34,
	},
]


def ensure_tax_parameters():
	"""Створює довідкові UA Tax Parameters, якщо їх ще немає (існуючі не перезаписує)."""
	for row in TAX_PARAMETERS:
		if frappe.db.exists(
			"UA Tax Parameters",
			{"year": row["year"], "single_tax_group": row["single_tax_group"]},
		):
			continue
		doc = frappe.new_doc("UA Tax Parameters")
		doc.update(row)
		doc.insert(ignore_permissions=True)
	frappe.db.commit()


POS_ROLES = ["POS Cashier", "POS Senior Cashier", "POS Manager", "POS Administrator", "PRRO Operator"]
APP_MODULES = ("UA FOP", "UA Fiscal", "UA POS")


def ensure_app_modules():
	"""Create modules added after the app was first installed.

	Frappe creates entries from ``modules.txt`` on a clean install, but an
	upgrade from an older app version can reach ``after_migrate`` without the
	new Module Def.  Pages and other linked records must only be created after
	their module exists.
	"""
	if not frappe.db.table_exists("Module Def"):
		return
	for module_name in APP_MODULES:
		if frappe.db.exists("Module Def", module_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Module Def",
				"module_name": module_name,
				"app_name": "erpnext_ua",
				"custom": 0,
			}
		).insert(ignore_permissions=True)
	frappe.db.commit()


def ensure_pos_page():
	"""Keep the Desk page present even when Frappe's orphan cleanup runs during migrate."""
	if not frappe.db.table_exists("Page"):
		return
	roles = ["POS Cashier", "POS Senior Cashier", "POS Manager", "POS Administrator", "System Manager"]
	if frappe.db.exists("Page", "ua-pos"):
		page = frappe.get_doc("Page", "ua-pos")
	else:
		page = frappe.new_doc("Page")
		page.page_name = "ua-pos"
	page.title = "UA POS"
	page.module = "UA POS"
	# This app is read-only in container deployments. A non-standard Page keeps
	# Frappe from exporting JSON on save and from removing it as an orphan.
	page.standard = "No"
	page.roles = []
	for role in roles:
		page.append("roles", {"role": role})
	if page.is_new():
		page.insert(ignore_permissions=True)
	else:
		page.save(ignore_permissions=True)
	frappe.db.commit()


def ensure_pos_setup():
	"""Ідемпотентно створює ролі та поля інтеграції POS без змін ERPNext core."""
	for role in POS_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)

	create_custom_fields(
		{
			"Employee": [
				{"fieldname": "ua_pos_barcode_hash", "label": "POS Barcode Hash", "fieldtype": "Data", "unique": 1},
				{"fieldname": "ua_pos_pin_hash", "label": "POS PIN Hash", "fieldtype": "Password"},
			],
			"Sales Invoice": [
				{"fieldname": "ua_pos_order", "label": "POS Order", "fieldtype": "Link", "options": "POS Order"},
				{"fieldname": "ua_pos_desk", "label": "POS Cash Desk", "fieldtype": "Link", "options": "POS Cash Desk"},
				{"fieldname": "ua_pos_shift", "label": "POS Operational Shift", "fieldtype": "Link", "options": "POS Operational Shift"},
				{"fieldname": "ua_fop_profile", "label": "FOP Profile", "fieldtype": "Link", "options": "FOP Profile"},
			],
			"Mode of Payment": [
				{"fieldname": "ua_pos_section", "label": "Українська каса та ПРРО", "fieldtype": "Section Break", "insert_after": "type"},
				{"fieldname": "ua_pos_enabled", "label": "Доступний у касі", "fieldtype": "Check", "default": "0", "in_list_view": 1, "insert_after": "ua_pos_section"},
				{"fieldname": "ua_pos_channel", "label": "Технічний канал оплати", "fieldtype": "Select", "options": "\nГотівка\nПлатіжний термінал\nІнтернет-еквайринг\nБанківський переказ\nПлатіжний сервіс\nПередоплата\nКредит / розстрочка\nСертифікат / замінник\nІнше", "insert_after": "ua_pos_enabled"},
				{"fieldname": "ua_prro_payment_form", "label": "Форма оплати ДПС", "fieldtype": "Select", "options": "\nГОТІВКА\nБЕЗГОТІВКОВА\nІНШЕ", "in_list_view": 1, "insert_after": "ua_pos_channel"},
				{"fieldname": "ua_prro_payment_means", "label": "Засіб оплати у фіскальному чеку", "fieldtype": "Data", "in_list_view": 1, "insert_after": "ua_prro_payment_form", "description": "Рядок 19 ФКЧ-1: Картка, LiqPay, Сертифікат тощо. Для готівки використовується ГОТІВКА."},
				{"fieldname": "ua_payformcd", "label": "Код PAYFORMCD у XML ДПС", "fieldtype": "Int", "insert_after": "ua_prro_payment_means", "description": "Код має відповідати чинній XSD ДПС. 0 — готівка, 1 — банківська картка, 2 — передоплата, 3 — кредит, 100000 — безготівковий платіжний інструмент."},
				{"fieldname": "ua_prro_code_verified", "label": "Код XML ДПС перевірено", "fieldtype": "Check", "default": "0", "insert_after": "ua_payformcd", "description": "Без цієї ознаки спосіб оплати не показується касиру."},
				{"fieldname": "ua_payment_rules_column", "fieldtype": "Column Break", "insert_after": "ua_prro_code_verified"},
				{"fieldname": "ua_allow_cashless", "label": "Дозволено для БЕЗГОТІВКОВОЇ форми", "fieldtype": "Check", "default": "0", "insert_after": "ua_payment_rules_column"},
				{"fieldname": "ua_allow_other", "label": "Дозволено для форми ІНШЕ", "fieldtype": "Check", "default": "0", "insert_after": "ua_allow_cashless"},
				{"fieldname": "ua_allow_prepayment", "label": "Дозволено для передоплати", "fieldtype": "Check", "default": "0", "insert_after": "ua_allow_other"},
				{"fieldname": "ua_allow_debt", "label": "Дозволено для боргу", "fieldtype": "Check", "default": "0", "insert_after": "ua_allow_prepayment"},
				{"fieldname": "ua_requires_terminal", "label": "Потрібен інтегрований платіжний термінал", "fieldtype": "Check", "default": "0", "insert_after": "ua_allow_debt"},
				{"fieldname": "ua_currency", "label": "Валюта каси", "fieldtype": "Link", "options": "Currency", "default": "UAH", "insert_after": "ua_requires_terminal"},
				{"fieldname": "ua_pos_kind", "label": "Застарілий технічний тип UA POS", "fieldtype": "Select", "options": "\nCash\nCard\nIBAN\nBonus\nInstallment", "hidden": 1, "insert_after": "ua_currency"},
			],
			"Item": [
				{"fieldname": "ua_serial_mode", "label": "UA Serial Mode", "fieldtype": "Select", "options": "\nStrict\nAdvisory\nNone"},
				{"fieldname": "ua_warranty_months", "label": "Warranty (months)", "fieldtype": "Int"},
				{"fieldname": "ua_prro_tax_letters", "label": "PRRO Tax Letters", "fieldtype": "Data", "description": "Літери податкових груп ДПС, наприклад А або АБ"},
				{"fieldname": "ua_prro_unit_code", "label": "PRRO Unit Code", "fieldtype": "Data"},
				{"fieldname": "ua_prro_dkpp", "label": "ДКПП", "fieldtype": "Data"},
			],
			"Sales Taxes and Charges": [
				{"fieldname": "ua_prro_tax_type", "label": "PRRO Tax Type", "fieldtype": "Int", "description": "0 — ПДВ, 1 — акциз та інші податки"},
				{"fieldname": "ua_prro_tax_letter", "label": "PRRO Tax Letter", "fieldtype": "Data"},
				{"fieldname": "ua_prro_tax_name", "label": "PRRO Tax Name", "fieldtype": "Data"},
			],
			"Customer": [
				{"fieldname": "ua_pos_details_section", "label": "UA POS Customer Details", "fieldtype": "Section Break"},
				{"fieldname": "ua_last_name", "label": "Прізвище", "fieldtype": "Data"},
				{"fieldname": "ua_first_name", "label": "Ім’я", "fieldtype": "Data"},
				{"fieldname": "ua_middle_name", "label": "По батькові", "fieldtype": "Data"},
				{"fieldname": "ua_gender", "label": "Стать", "fieldtype": "Link", "options": "Gender"},
				{"fieldname": "ua_date_of_birth", "label": "Дата народження", "fieldtype": "Date"},
				{"fieldname": "ua_city", "label": "Місто", "fieldtype": "Data"},
				{"fieldname": "ua_pos_comment", "label": "Коментар касира", "fieldtype": "Small Text"},
				{"fieldname": "ua_telegram_chat_id", "label": "Telegram Chat ID", "fieldtype": "Data", "hidden": 1, "read_only": 1},
			],
		},
		update=True,
	)
	ensure_payment_method_catalog()
	frappe.db.commit()


def ensure_payment_method_catalog():
	"""Завантажує каталог засобів оплати, не активуючи неперевірені канали."""
	from erpnext_ua.ua_fiscal.payment_catalog import BASE_PAYMENT_METHODS, CHANNEL_KIND, PRRO_PAYMENT_CATALOG

	def values(config: dict) -> dict:
		return {
			"ua_pos_channel": config["channel"],
			"ua_prro_payment_form": config["form"],
			"ua_prro_payment_means": config.get("means") or config["name"],
			"ua_payformcd": config["code"],
			"ua_allow_cashless": config["allow_cashless"],
			"ua_allow_other": config["allow_other"],
			"ua_allow_prepayment": config["allow_prepayment"],
			"ua_allow_debt": config["allow_debt"],
			"ua_requires_terminal": config["requires_terminal"],
			"ua_currency": "UAH",
			"ua_pos_kind": CHANNEL_KIND[config["channel"]],
		}

	for config in BASE_PAYMENT_METHODS:
		if not frappe.db.exists("Mode of Payment", config["name"]):
			frappe.get_doc(
				{
					"doctype": "Mode of Payment",
					"mode_of_payment": config["name"],
					"type": config["mop_type"],
					"enabled": 1,
					**values(config),
					"ua_pos_enabled": 1,
					"ua_prro_code_verified": 1,
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value(
				"Mode of Payment",
				config["name"],
				{**values(config), "ua_pos_enabled": 1, "ua_prro_code_verified": 1},
				update_modified=False,
			)

	for config in PRRO_PAYMENT_CATALOG:
		if frappe.db.exists("Mode of Payment", config["name"]):
			# Каталог задає початкові значення лише один раз. Після того як
			# адміністратор налаштував канал, наступна міграція не має права
			# перезаписати перевірені ним код, форму, назву або дозволи.
			if not frappe.db.get_value("Mode of Payment", config["name"], "ua_pos_channel"):
				frappe.db.set_value("Mode of Payment", config["name"], values(config), update_modified=False)
			continue
		frappe.get_doc(
			{
				"doctype": "Mode of Payment",
				"mode_of_payment": config["name"],
				"type": config["mop_type"],
				"enabled": 0,
				**values(config),
				"ua_pos_enabled": 0,
				"ua_prro_code_verified": 0,
			}
		).insert(ignore_permissions=True)


def ensure_prro_setup():
	"""Заповнює стабільні 64-символьні device ID для існуючих ПРРО без мережевих викликів."""
	if not frappe.db.table_exists("PRRO Cash Register"):
		return
	for row in frappe.get_all("PRRO Cash Register", fields=["name", "device_id"]):
		if re.fullmatch(r"[0-9a-f]{64}", row.device_id or ""):
			continue
		seed = (row.device_id or str(uuid.uuid4())).encode()
		frappe.db.set_value(
			"PRRO Cash Register",
			row.name,
			{
				"device_id": hashlib.sha256(seed).hexdigest(),
				"device_registered": 0,
				"device_registered_at": None,
				"runtime_state": "Online",
			},
			update_modified=False,
		)
	frappe.db.commit()


def ensure_pos_printers():
	"""Переносить legacy host:port кас у керований довідник принтерів без втрати налаштувань."""
	if not frappe.db.table_exists("POS Printer") or not frappe.db.table_exists("POS Cash Desk"):
		return
	for desk in frappe.get_all(
		"POS Cash Desk",
		filters={"receipt_printer": ("is", "not set"), "receipt_printer_host": ("is", "set")},
		fields=["name", "desk_name", "receipt_printer_host", "receipt_printer_port"],
	):
		printer_name = f"Чековий принтер — {desk.desk_name or desk.name}"
		if not frappe.db.exists("POS Printer", printer_name):
			try:
				frappe.get_doc(
					{
						"doctype": "POS Printer",
						"printer_name": printer_name,
						"printer_type": "Receipt",
						"connection_type": "Network ESC/POS",
						"host": desk.receipt_printer_host,
						"port": desk.receipt_printer_port or 9100,
					}
				).insert(ignore_permissions=True)
			except frappe.ValidationError:
				# Невалідний або публічний legacy-host не повинен зривати bench migrate.
				# Адміністратор виправить його вручну; небезпечний endpoint не активується.
				frappe.log_error(
					frappe.get_traceback(), f"POS Printer migration skipped for {desk.name}"
				)
				continue
		frappe.db.set_value("POS Cash Desk", desk.name, "receipt_printer", printer_name, update_modified=False)
	frappe.db.commit()
