import frappe

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
