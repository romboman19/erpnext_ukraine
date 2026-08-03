from __future__ import annotations

import unittest
from datetime import date

from erpnext_ua.ua_fop.tax_rules import (
	TaxAmounts,
	build_deadline_rows,
	missing_parameter_fields,
)

AMOUNTS = TaxAmounts(
	single_tax_monthly=332.80,
	military_levy_monthly=864.70,
	esv_monthly=1902.34,
	single_tax_percent_no_vat=5,
	single_tax_percent_vat=3,
	military_levy_percent=1,
)


def row_for(rows, tax_type: str, period_label: str):
	return next(row for row in rows if row.tax_type == tax_type and row.period_label == period_label)


class TestUATaxRules(unittest.TestCase):
	def test_group_one_advances_do_not_move_forward_from_a_weekend(self):
		rows = build_deadline_rows(2026, "1", AMOUNTS)

		single_tax = row_for(rows, "Єдиний податок", "червень 2026")
		military_levy = row_for(rows, "Військовий збір", "червень 2026")
		self.assertEqual(single_tax.statutory_due_date, date(2026, 6, 20))
		self.assertEqual(single_tax.due_date, date(2026, 6, 19))
		self.assertEqual(military_levy.statutory_due_date, date(2026, 6, 20))
		self.assertEqual(military_levy.due_date, date(2026, 6, 19))

	def test_group_three_q1_matches_the_official_2026_calendar(self):
		rows = build_deadline_rows(2026, "3", AMOUNTS)

		declaration = row_for(rows, "Декларація ЄП", "1 квартал 2026")
		payment = row_for(rows, "Єдиний податок", "1 квартал 2026")
		self.assertEqual(declaration.statutory_due_date, date(2026, 5, 10))
		self.assertEqual(declaration.due_date, date(2026, 5, 11))
		self.assertEqual(payment.due_date, date(2026, 5, 20))

	def test_esv_is_due_by_the_nineteenth_and_moves_forward_from_weekend(self):
		rows = build_deadline_rows(2026, "2", AMOUNTS)

		q1 = row_for(rows, "ЄСВ", "1 квартал 2026")
		q3 = row_for(rows, "ЄСВ", "3 квартал 2026")
		q4 = row_for(rows, "ЄСВ", "4 квартал 2026")
		self.assertEqual(q1.statutory_due_date, date(2026, 4, 19))
		self.assertEqual(q1.due_date, date(2026, 4, 20))
		self.assertEqual(q3.due_date, date(2026, 10, 19))
		self.assertEqual(q4.due_date, date(2027, 1, 19))
		self.assertEqual(q4.amount, 5707.02)

	def test_group_one_calendar_has_all_expected_events(self):
		self.assertEqual(len(build_deadline_rows(2026, "1", AMOUNTS)), 29)
		self.assertEqual(len(build_deadline_rows(2026, "3", AMOUNTS)), 16)

	def test_parameter_completeness_is_group_specific(self):
		values = {
			"minimum_wage": 8647,
			"subsistence_minimum": 3328,
			"income_limit": 1_444_049,
			"single_tax_monthly": 332.80,
			"military_levy_monthly": 864.70,
			"esv_monthly": 1902.34,
			"official_sources": "https://tax.gov.ua/example",
			"verified_on": "2026-08-03",
		}
		self.assertEqual(missing_parameter_fields("1", values), ())
		del values["official_sources"]
		self.assertEqual(missing_parameter_fields("1", values), ("official_sources",))


if __name__ == "__main__":
	unittest.main()
