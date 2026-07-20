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


def ensure_accounting_setup():
	"""Install non-destructive metadata for Ukrainian statutory charts.

	The hook only adds fields. It never applies or replaces a company's chart;
	that remains an explicit, confirmed action in UA Chart of Accounts Setup.
	"""
	create_custom_fields(
		{
			"Company": [
				{
					"fieldname": "ua_accounting_section",
					"label": "Український план рахунків",
					"fieldtype": "Section Break",
					"insert_after": "default_currency",
				},
				{
					"fieldname": "ua_chart_template",
					"label": "Шаблон плану рахунків України",
					"fieldtype": "Select",
					"options": "\nfull_291\nsimplified_186",
					"read_only": 1,
					"insert_after": "ua_accounting_section",
				},
				{
					"fieldname": "ua_chart_revision",
					"label": "Редакція нормативної бази",
					"fieldtype": "Data",
					"read_only": 1,
					"insert_after": "ua_chart_template",
				},
				{
					"fieldname": "ua_chart_applied_on",
					"label": "План застосовано",
					"fieldtype": "Datetime",
					"read_only": 1,
					"insert_after": "ua_chart_revision",
				},
			],
			"Account": [
				{
					"fieldname": "ua_chart_template",
					"label": "Шаблон плану рахунків України",
					"fieldtype": "Data",
					"read_only": 1,
					"insert_after": "account_number",
				},
				{
					"fieldname": "ua_legal_source",
					"label": "Джерело рахунку",
					"fieldtype": "Select",
					"options": "\nofficial\nerpnext_extension",
					"read_only": 1,
					"insert_after": "ua_chart_template",
				},
				{
					"fieldname": "ua_off_balance",
					"label": "Позабалансовий рахунок",
					"fieldtype": "Check",
					"read_only": 1,
					"insert_after": "ua_legal_source",
				},
			],
		},
		update=True,
	)
	frappe.db.commit()
