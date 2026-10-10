"""ЄСВ «за себе» у податковому календарі: звільнення, пільги, ідемпотентність.

Ці перевірки торкаються бази, тому живуть тут, а не у framework-independent
наборі. Чисту арифметику пільги перевіряє `erpnext_ua/tests/test_ua_tax_rules.py`.
"""

from __future__ import annotations

import unittest
from random import randrange
from uuid import uuid4

# Див. test_ua_off_balance_entry: у sys.modules може вже бути stub `frappe`.
try:
	import frappe
	from frappe.tests import IntegrationTestCase

	from erpnext_ua.ua_fop.doctype.fop_profile.fop_profile import RNOKPP_WEIGHTS
	from erpnext_ua.ua_fop.tax_calendar import (
		CANCELLED_STATUS,
		_generate_deadlines,
		esv_context,
		update_statuses_and_notify,
	)
	from erpnext_ua.ua_fop.tax_rules import (
		ESV_MODE_ABOVE_MINIMUM,
		ESV_MODE_EXEMPT,
		ESV_MODE_MINIMUM,
	)
except ModuleNotFoundError:
	frappe = None
	IntegrationTestCase = unittest.TestCase

YEAR = 2026
# 22% × 8 647 грн мінімальної зарплати 2026 року
# (ст. 8 ЗУ № 4695-IX від 03.12.2025; ст. 8 ч. 5 ЗУ № 2464-VI).
ESV_MONTHLY = 1902.34
ESV_QUARTER = 5707.02
ESV_SOURCE = "https://zakon.rada.gov.ua/laws/show/2464-17"


def synthetic_rnokpp() -> str:
	"""Випадковий РНОКПП із коректною контрольною сумою; нікому не належить."""
	base = f"{randrange(100_000_000, 999_999_999)}"
	digits = [int(digit) for digit in base]
	control = sum(digit * weight for digit, weight in zip(digits, RNOKPP_WEIGHTS)) % 11 % 10
	return f"{base}{control}"


@unittest.skipIf(frappe is None, "requires a Frappe test site")
class TestESVCalendar(IntegrationTestCase):
	def setUp(self):
		suffix = uuid4().hex[:5].upper()
		self.company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": f"_UA ESV Test {suffix}",
				"abbr": f"E{suffix[:4]}",
				"country": "Ukraine",
				"default_currency": "UAH",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)
		self.profile = frappe.get_doc(
			{
				"doctype": "FOP Profile",
				"company": self.company.name,
				"fop_full_name": "Тестовий Тест Тестович",
				"prro_registered_name": "ФОП Тестовий Тест Тестович",
				"tax_id": synthetic_rnokpp(),
				"status": "Active",
				"single_tax_group": "3",
				"tax_rate_mode": "5% без ПДВ",
				"esv_mode": ESV_MODE_MINIMUM,
			}
		).insert(ignore_permissions=True)

	def test_a_plain_profile_still_gets_four_quarterly_esv_rows(self):
		_generate_deadlines(self.profile.name, YEAR)

		rows = self._esv_rows()
		self.assertEqual([row.period_label for row in rows], self._quarter_labels())
		self.assertEqual([row.amount for row in rows], [ESV_QUARTER] * 4)
		self.assertEqual(len(self._rows("Єдиний податок")), 4)
		self.assertEqual(len(self._rows("Військовий збір")), 4)

	def test_an_exempt_profile_has_its_existing_esv_rows_cancelled_not_left_hanging(self):
		_generate_deadlines(self.profile.name, YEAR)
		self.assertEqual(len(self._esv_rows()), 4)
		single_tax_before = self._fingerprint("Єдиний податок")
		levy_before = self._fingerprint("Військовий збір")

		self._make_exempt(f"{YEAR}-01-01")
		result = _generate_deadlines(self.profile.name, YEAR)

		self.assertEqual(result["cancelled"], 4)
		self.assertEqual([row.status for row in self._esv_rows()], [CANCELLED_STATUS] * 4)
		# ЄП і ВЗ поза обсягом пільги по ЄСВ і не змінюються.
		self.assertEqual(self._fingerprint("Єдиний податок"), single_tax_before)
		self.assertEqual(self._fingerprint("Військовий збір"), levy_before)

	def test_regenerating_for_an_exempt_profile_neither_duplicates_nor_revives_rows(self):
		_generate_deadlines(self.profile.name, YEAR)
		self._make_exempt(f"{YEAR}-01-01")
		_generate_deadlines(self.profile.name, YEAR)

		second = _generate_deadlines(self.profile.name, YEAR)

		self.assertEqual(second["cancelled"], 0)
		rows = self._esv_rows()
		self.assertEqual(len(rows), 4)
		self.assertEqual([row.status for row in rows], [CANCELLED_STATUS] * 4)

	def test_the_status_sweep_does_not_resurrect_a_cancelled_esv_row(self):
		_generate_deadlines(self.profile.name, YEAR)
		self._make_exempt(f"{YEAR}-01-01")
		_generate_deadlines(self.profile.name, YEAR)

		update_statuses_and_notify()

		self.assertEqual([row.status for row in self._esv_rows()], [CANCELLED_STATUS] * 4)

	def test_a_mid_year_exemption_keeps_the_payable_part_of_the_quarter(self):
		_generate_deadlines(self.profile.name, YEAR)
		self._make_exempt(f"{YEAR}-05-01")

		result = _generate_deadlines(self.profile.name, YEAR)

		self.assertEqual(result["cancelled"], 2)
		rows = {row.period_label: row for row in self._esv_rows()}
		self.assertEqual(rows[f"1 квартал {YEAR}"].amount, ESV_QUARTER)
		self.assertEqual(rows[f"2 квартал {YEAR}"].amount, ESV_MONTHLY)
		self.assertEqual(rows[f"3 квартал {YEAR}"].status, CANCELLED_STATUS)
		self.assertEqual(rows[f"4 квартал {YEAR}"].status, CANCELLED_STATUS)

	def test_a_paid_quarter_is_never_cancelled_by_a_later_exemption(self):
		_generate_deadlines(self.profile.name, YEAR)
		paid = frappe.db.get_value(
			"UA Tax Deadline",
			{
				"fop_profile": self.profile.name,
				"tax_type": "ЄСВ",
				"period_label": f"1 квартал {YEAR}",
			},
		)
		frappe.db.set_value("UA Tax Deadline", paid, "status", "Виконано")

		self._make_exempt(f"{YEAR}-01-01")
		result = _generate_deadlines(self.profile.name, YEAR)

		self.assertEqual(result["cancelled"], 3)
		self.assertEqual(frappe.db.get_value("UA Tax Deadline", paid, "status"), "Виконано")
		self.assertEqual(
			frappe.utils.flt(frappe.db.get_value("UA Tax Deadline", paid, "amount"), 2), ESV_QUARTER
		)

	def test_removing_the_exemption_brings_the_cancelled_rows_back(self):
		_generate_deadlines(self.profile.name, YEAR)
		self._make_exempt(f"{YEAR}-01-01")
		_generate_deadlines(self.profile.name, YEAR)

		self.profile.esv_mode = ESV_MODE_MINIMUM
		self.profile.save(ignore_permissions=True)
		_generate_deadlines(self.profile.name, YEAR)

		rows = self._esv_rows()
		self.assertEqual(len(rows), 4)
		self.assertEqual([row.status for row in rows], ["Заплановано"] * 4)
		self.assertEqual([row.amount for row in rows], [ESV_QUARTER] * 4)

	def test_a_confirmed_above_minimum_amount_is_used_instead_of_the_yearly_minimum(self):
		self._confirm_override(2500, YEAR, mode=ESV_MODE_ABOVE_MINIMUM)

		_generate_deadlines(self.profile.name, YEAR)

		self.assertEqual([row.amount for row in self._esv_rows()], [7500.0] * 4)

	def test_a_confirmed_amount_from_another_year_is_not_applied(self):
		self._confirm_override(2500, YEAR - 1)

		self.assertIsNone(esv_context(self.profile, YEAR).monthly_override)
		_generate_deadlines(self.profile.name, YEAR)
		self.assertEqual([row.amount for row in self._esv_rows()], [ESV_QUARTER] * 4)

	def test_an_exemption_needs_a_reason_before_it_can_be_saved(self):
		self.profile.esv_mode = ESV_MODE_EXEMPT
		self.profile.esv_exemption_from = f"{YEAR}-01-01"

		with self.assertRaisesRegex(frappe.ValidationError, "підставу"):
			self.profile.save(ignore_permissions=True)

	def test_an_exemption_needs_a_first_month_before_it_can_be_saved(self):
		self.profile.esv_mode = ESV_MODE_EXEMPT
		self.profile.esv_exemption_reason = "Пенсіонер за віком"

		with self.assertRaisesRegex(frappe.ValidationError, "перший місяць"):
			self.profile.save(ignore_permissions=True)

	def test_an_above_minimum_amount_needs_year_verification_date_and_source(self):
		self.profile.esv_mode = ESV_MODE_ABOVE_MINIMUM
		self.profile.esv_monthly_override = 2500

		with self.assertRaisesRegex(frappe.ValidationError, "підтвердження суми ЄСВ"):
			self.profile.save(ignore_permissions=True)

	def test_a_non_official_esv_source_is_rejected(self):
		self.profile.esv_monthly_override = 2500
		self.profile.esv_rate_year = YEAR
		self.profile.esv_verified_on = f"{YEAR}-10-10"
		self.profile.esv_sources = "https://example.com/blog"

		with self.assertRaisesRegex(frappe.ValidationError, "gov.ua"):
			self.profile.save(ignore_permissions=True)

	def test_leaving_the_exempt_mode_clears_the_exemption_fields(self):
		self._make_exempt(f"{YEAR}-05-01")

		self.profile.esv_mode = ESV_MODE_MINIMUM
		self.profile.save(ignore_permissions=True)

		self.assertIsNone(self.profile.esv_exemption_reason)
		self.assertIsNone(self.profile.esv_exemption_from)

	def _confirm_override(self, amount: float, year: int, mode: str = ESV_MODE_MINIMUM):
		self.profile.esv_mode = mode
		self.profile.esv_monthly_override = amount
		self.profile.esv_rate_year = year
		self.profile.esv_verified_on = f"{year}-10-10"
		self.profile.esv_sources = ESV_SOURCE
		self.profile.save(ignore_permissions=True)

	def _make_exempt(self, exemption_from: str):
		self.profile.esv_mode = ESV_MODE_EXEMPT
		self.profile.esv_exemption_reason = "Пенсіонер за віком"
		self.profile.esv_exemption_from = exemption_from
		self.profile.save(ignore_permissions=True)

	def _quarter_labels(self) -> list[str]:
		return [f"{quarter} квартал {YEAR}" for quarter in range(1, 5)]

	def _rows(self, tax_type: str):
		return frappe.get_all(
			"UA Tax Deadline",
			filters={"fop_profile": self.profile.name, "tax_type": tax_type},
			fields=["name", "period_label", "amount", "status", "due_date"],
			order_by="due_date asc",
		)

	def _esv_rows(self):
		return self._rows("ЄСВ")

	def _fingerprint(self, tax_type: str):
		return [
			(row.period_label, frappe.utils.flt(row.amount, 2), row.status, str(row.due_date))
			for row in self._rows(tax_type)
		]
