from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from erpnext_ua.ua_accounting.chart_of_accounts import (
	build_erpnext_tree,
	load_template,
	template_summary,
)


LEDGER_HISTORY = (
	("GL Entry", "бухгалтерські проводки"),
	("Stock Ledger Entry", "рухи складського обліку"),
)

SUBMITTED_DOCUMENTS = (
	("Sales Invoice", "проведені рахунки продажу"),
	("Purchase Invoice", "проведені рахунки закупівлі"),
	("Payment Entry", "проведені платежі"),
	("Journal Entry", "проведені журнальні операції"),
	("Delivery Note", "проведені накладні доставки"),
	("Purchase Receipt", "проведені надходження"),
	("Stock Entry", "проведені складські операції"),
	("Sales Order", "проведені замовлення продажу"),
	("Purchase Order", "проведені замовлення закупівлі"),
)


def _count(doctype: str, filters: dict | None = None) -> int:
	if not frappe.db.table_exists(doctype):
		return 0
	return frappe.db.count(doctype, filters=filters or {})


def preflight(company: str, template_value: str) -> dict:
	if not frappe.db.exists("Company", company):
		frappe.throw(_("Компанію {0} не знайдено").format(frappe.bold(company)))

	template = load_template(template_value)
	company_country = frappe.db.get_value("Company", company, "country")
	blockers = []
	warnings = []
	warnings.append(
		"Рахунки класу 0 буде імпортовано вимкненими: стандартний GL ERPNext працює подвійним записом "
		"і не є позабалансовим регістром простої системи"
	)

	if company_country != "Ukraine":
		blockers.append(f"Країна компанії має бути Ukraine, зараз: {company_country or 'не задано'}")

	for doctype, label in LEDGER_HISTORY:
		count = _count(doctype, {"company": company})
		if count:
			blockers.append(f"{label}: {count}. План не можна замінювати після появи історії реєстру")

	for doctype, label in SUBMITTED_DOCUMENTS:
		count = _count(doctype, {"company": company, "docstatus": 1})
		if count:
			blockers.append(f"{label}: {count}")

	if frappe.db.table_exists("POS Cash Desk") and frappe.db.table_exists("POS Order"):
		desks = frappe.get_all("POS Cash Desk", filters={"company": company}, pluck="name")
		if desks:
			count = _count("POS Order", {"cash_desk": ("in", desks)})
			if count:
				blockers.append(f"POS-замовлення: {count}")

	if frappe.db.table_exists("POS Profile"):
		count = _count("POS Profile", {"company": company})
		if count:
			warnings.append(
				f"POS-профілі: {count}. Після застосування перевірте їхні рахунки доходів, витрат і способів оплати"
			)

	if frappe.db.table_exists("Bank Account"):
		linked_banks = _count("Bank Account", {"company": company, "account": ("is", "set")})
		if linked_banks:
			blockers.append(f"Банківські рахунки, прив'язані до старих рахунків обліку: {linked_banks}")

	summary = template_summary(template)
	return {
		"allowed": not blockers,
		"company": company,
		"country": company_country,
		"existing_account_count": _count("Account", {"company": company}),
		"blockers": blockers,
		"warnings": warnings,
		"template": summary,
		"checked_at": str(now_datetime()),
	}


def _only_accounting_administrators() -> None:
	frappe.only_for(["Accounts Manager", "System Manager"])


def _set_company_defaults(company: str, template: dict) -> dict:
	resolved = {}
	for fieldname, account_number in template["defaults"].items():
		account = frappe.db.get_value(
			"Account",
			{"company": company, "account_number": account_number, "is_group": 0},
			"name",
		)
		if not account:
			frappe.throw(
				_("Не знайдено операційний рахунок {0} для поля {1}").format(
					frappe.bold(account_number), frappe.bold(fieldname)
				)
			)
		resolved[fieldname] = account

	valid_company_fields = {field.fieldname for field in frappe.get_meta("Company").fields}
	values = {fieldname: account for fieldname, account in resolved.items() if fieldname in valid_company_fields}
	values.update(
		{
			"ua_chart_template": template["key"],
			"ua_chart_revision": max(row["revision"] for row in template["legal_basis"]),
			"ua_chart_applied_on": now_datetime(),
		}
	)
	frappe.db.set_value("Company", company, values, update_modified=True)
	return resolved


def _mark_accounts(company: str, template: dict) -> None:
	for row in template["accounts"]:
		name = frappe.db.get_value(
			"Account", {"company": company, "account_number": row["code"]}, "name"
		)
		if not name:
			continue
		frappe.db.set_value(
			"Account",
			name,
			{
				"ua_chart_template": template["key"],
				"ua_legal_source": row["source"],
				"ua_off_balance": 1 if row["code"].startswith("0") else 0,
				"disabled": 1 if row["code"].startswith("0") else 0,
			},
			update_modified=False,
		)

	off_balance_root = frappe.db.get_value(
		"Account",
		{
			"company": company,
			"account_name": template["groups"]["0"]["name"],
			"parent_account": ("is", "not set"),
		},
		"name",
	)
	if off_balance_root:
		frappe.db.set_value(
			"Account",
			off_balance_root,
			{
				"ua_chart_template": template["key"],
				"ua_legal_source": "official",
				"ua_off_balance": 1,
				"disabled": 1,
			},
			update_modified=False,
		)


def apply_chart(company: str, template_value: str, confirmation: str) -> dict:
	"""Replace an unused company's chart in one rollback-safe transaction.

	This deliberately refuses companies with any GL/SLE history, including
	cancelled rows.  Migrating an operating company requires a separate,
	audited opening-balance project and must not be disguised as chart setup.
	"""
	_only_accounting_administrators()
	frappe.has_permission("Company", "write", company, throw=True)
	if (confirmation or "").strip() != company:
		frappe.throw(_("Для підтвердження введіть точну назву компанії: {0}").format(frappe.bold(company)))

	check = preflight(company, template_value)
	if not check["allowed"]:
		frappe.throw(
			_("План рахунків не застосовано:<br>{0}").format("<br>".join(f"• {row}" for row in check["blockers"])),
			title=_("Заміна заблокована"),
		)

	template = load_template(template_value)
	tree = build_erpnext_tree(template)
	savepoint = "ua_chart_of_accounts_apply"
	frappe.db.savepoint(savepoint)
	previous_flag = getattr(frappe.local.flags, "ignore_root_company_validation", None)
	try:
		from erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts import create_charts
		from erpnext.accounts.doctype.chart_of_accounts_importer.chart_of_accounts_importer import (
			set_default_accounts,
			unset_existing_data,
		)

		unset_existing_data(company)
		frappe.local.flags.ignore_root_company_validation = True
		create_charts(company, custom_chart=tree)
		set_default_accounts(company)
		defaults = _set_company_defaults(company, template)
		_mark_accounts(company, template)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	finally:
		frappe.local.flags.ignore_root_company_validation = previous_flag

	return {
		"company": company,
		"template": template_summary(template),
		"created_account_count": _count("Account", {"company": company}),
		"defaults": defaults,
		"applied_at": str(now_datetime()),
		"warnings": check["warnings"],
	}
