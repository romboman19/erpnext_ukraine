from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company:
		frappe.throw(_("Company is required"))
	filters.to_date = getdate(filters.to_date or nowdate())
	filters.from_date = getdate(filters.from_date or "1000-01-01")
	if filters.from_date > filters.to_date:
		frappe.throw(_("From Date cannot be after To Date"))

	conditions = ["entry.docstatus = 1", "entry.company = %(company)s", "entry.posting_date <= %(to_date)s"]
	for fieldname in ("off_balance_account", "party_type", "party", "item_code", "warehouse"):
		if filters.get(fieldname):
			conditions.append(f"entry.{fieldname} = %({fieldname})s")

	data = frappe.db.sql(
		f"""
		SELECT
			entry.off_balance_account,
			account.account_number,
			entry.party_type,
			entry.party,
			entry.item_code,
			entry.warehouse,
			entry.uom,
			entry.currency,
			SUM(CASE
				WHEN entry.posting_date < %(from_date)s AND entry.direction = 'Increase' THEN entry.quantity
				WHEN entry.posting_date < %(from_date)s AND entry.direction = 'Decrease' THEN -entry.quantity
				ELSE 0 END) AS opening_quantity,
			SUM(CASE WHEN entry.posting_date >= %(from_date)s AND entry.direction = 'Increase'
				THEN entry.quantity ELSE 0 END) AS increase_quantity,
			SUM(CASE WHEN entry.posting_date >= %(from_date)s AND entry.direction = 'Decrease'
				THEN entry.quantity ELSE 0 END) AS decrease_quantity,
			SUM(CASE WHEN entry.direction = 'Increase' THEN entry.quantity ELSE -entry.quantity END)
				AS closing_quantity,
			SUM(CASE
				WHEN entry.posting_date < %(from_date)s AND entry.direction = 'Increase' THEN entry.amount
				WHEN entry.posting_date < %(from_date)s AND entry.direction = 'Decrease' THEN -entry.amount
				ELSE 0 END) AS opening_amount,
			SUM(CASE WHEN entry.posting_date >= %(from_date)s AND entry.direction = 'Increase'
				THEN entry.amount ELSE 0 END) AS increase_amount,
			SUM(CASE WHEN entry.posting_date >= %(from_date)s AND entry.direction = 'Decrease'
				THEN entry.amount ELSE 0 END) AS decrease_amount,
			SUM(CASE WHEN entry.direction = 'Increase' THEN entry.amount ELSE -entry.amount END)
				AS closing_amount
		FROM `tabUA Off Balance Entry` entry
		INNER JOIN `tabAccount` account ON account.name = entry.off_balance_account
		WHERE {" AND ".join(conditions)}
		GROUP BY
			entry.off_balance_account, account.account_number, entry.party_type, entry.party,
			entry.item_code, entry.warehouse, entry.uom, entry.currency
		HAVING
			ABS(opening_quantity) > 0.000001 OR ABS(increase_quantity) > 0.000001
			OR ABS(decrease_quantity) > 0.000001 OR ABS(closing_quantity) > 0.000001
			OR ABS(opening_amount) > 0.000001 OR ABS(increase_amount) > 0.000001
			OR ABS(decrease_amount) > 0.000001 OR ABS(closing_amount) > 0.000001
		ORDER BY account.account_number, entry.party, entry.item_code, entry.warehouse
		""",
		filters,
		as_dict=True,
	)
	return get_columns(), data


def get_columns():
	return [
		{"fieldname": "account_number", "label": _("Рахунок"), "fieldtype": "Data", "width": 90},
		{
			"fieldname": "off_balance_account",
			"label": _("Позабалансовий рахунок"),
			"fieldtype": "Link",
			"options": "Account",
			"width": 240,
		},
		{"fieldname": "party_type", "label": _("Тип контрагента"), "fieldtype": "Data", "width": 130},
		{"fieldname": "party", "label": _("Контрагент"), "fieldtype": "Dynamic Link", "options": "party_type", "width": 180},
		{"fieldname": "item_code", "label": _("Товар"), "fieldtype": "Link", "options": "Item", "width": 160},
		{"fieldname": "warehouse", "label": _("Склад"), "fieldtype": "Link", "options": "Warehouse", "width": 160},
		{"fieldname": "uom", "label": _("Од. виміру"), "fieldtype": "Link", "options": "UOM", "width": 90},
		{"fieldname": "opening_quantity", "label": _("Початкова кількість"), "fieldtype": "Float", "width": 130},
		{"fieldname": "increase_quantity", "label": _("Надійшло"), "fieldtype": "Float", "width": 110},
		{"fieldname": "decrease_quantity", "label": _("Вибуло"), "fieldtype": "Float", "width": 110},
		{"fieldname": "closing_quantity", "label": _("Кінцева кількість"), "fieldtype": "Float", "width": 130},
		{"fieldname": "currency", "label": _("Валюта"), "fieldtype": "Link", "options": "Currency", "width": 80},
		{
			"fieldname": "opening_amount",
			"label": _("Початкова вартість"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 140,
		},
		{
			"fieldname": "increase_amount",
			"label": _("Надійшло, вартість"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 140,
		},
		{
			"fieldname": "decrease_amount",
			"label": _("Вибуло, вартість"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 140,
		},
		{
			"fieldname": "closing_amount",
			"label": _("Кінцева вартість"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 140,
		},
	]
