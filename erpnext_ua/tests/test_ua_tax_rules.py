from __future__ import annotations

import unittest
from datetime import date

from erpnext_ua.ua_fop.tax_rules import (
	ESV_MODE_ABOVE_MINIMUM,
	ESV_MODE_EXEMPT,
	ESV_MODE_MINIMUM,
	ESVContext,
	TaxAmounts,
	build_deadline_rows,
	esv_period_labels,
	is_official_source,
	missing_parameter_fields,
	official_source_urls,
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


def rows_of_type(rows, tax_type: str):
	return [row for row in rows if row.tax_type == tax_type]


def fingerprint(rows, tax_type: str):
	"""Порівнюваний знімок рядків одного типу — для регресу ЄП і ВЗ."""
	return [
		(row.period_label, row.statutory_due_date, row.due_date, row.amount, row.notes)
		for row in rows_of_type(rows, tax_type)
	]


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

	def test_default_esv_context_matches_the_behaviour_before_exemptions(self):
		baseline = build_deadline_rows(2026, "2", AMOUNTS)
		explicit = build_deadline_rows(2026, "2", AMOUNTS, ESVContext(mode=ESV_MODE_MINIMUM))

		self.assertEqual(len(rows_of_type(baseline, "ЄСВ")), 4)
		self.assertEqual(fingerprint(baseline, "ЄСВ"), fingerprint(explicit, "ЄСВ"))
		self.assertEqual(
			[row.amount for row in rows_of_type(explicit, "ЄСВ")], [5707.02] * 4
		)

	def test_a_fop_exempt_all_year_gets_no_esv_rows_at_all(self):
		exempt = ESVContext(
			mode=ESV_MODE_EXEMPT,
			exemption_from=date(2025, 3, 1),
			exemption_reason="Пенсіонер за віком",
		)
		baseline = build_deadline_rows(2026, "1", AMOUNTS)
		rows = build_deadline_rows(2026, "1", AMOUNTS, exempt)

		self.assertEqual(rows_of_type(rows, "ЄСВ"), [])
		self.assertEqual(len(rows), 25)
		# ЄП, ВЗ і декларація поза обсягом цієї пільги й не змінюються.
		for tax_type in ("Єдиний податок", "Військовий збір", "Декларація ЄП"):
			self.assertEqual(fingerprint(rows, tax_type), fingerprint(baseline, tax_type))

	def test_exemption_starting_mid_year_is_charged_by_month_not_by_quarter(self):
		# Пільга з травня 2026: квітень оплачується, травень і червень — ні.
		exempt = ESVContext(
			mode=ESV_MODE_EXEMPT,
			exemption_from=date(2026, 5, 1),
			exemption_reason="Пенсіонер за віком",
		)
		rows = build_deadline_rows(2026, "3", AMOUNTS, exempt)
		esv = rows_of_type(rows, "ЄСВ")

		self.assertEqual([row.period_label for row in esv], ["1 квартал 2026", "2 квартал 2026"])
		self.assertEqual(esv[0].amount, 5707.02)
		self.assertEqual(esv[1].amount, 1902.34)
		self.assertEqual(esv[1].due_date, date(2026, 7, 20))
		self.assertIn("квітень 2026", esv[1].notes)
		self.assertIn("Пенсіонер за віком", esv[1].notes)

	def test_exemption_ending_mid_year_charges_only_the_months_after_it(self):
		# Пільга скінчилася у серпні 2026: вересень і весь IV квартал платні.
		exempt = ESVContext(
			mode=ESV_MODE_EXEMPT,
			exemption_from=date(2026, 1, 1),
			exemption_to=date(2026, 8, 31),
			exemption_reason="ЄСВ сплачує роботодавець за основним місцем роботи",
		)
		esv = rows_of_type(build_deadline_rows(2026, "2", AMOUNTS, exempt), "ЄСВ")

		self.assertEqual([row.period_label for row in esv], ["3 квартал 2026", "4 квартал 2026"])
		self.assertEqual(esv[0].amount, 1902.34)
		self.assertEqual(esv[1].amount, 5707.02)
		self.assertEqual(esv[1].due_date, date(2027, 1, 19))

	def test_a_single_exempt_month_still_leaves_two_payable_months(self):
		exempt = ESVContext(
			mode=ESV_MODE_EXEMPT,
			exemption_from=date(2026, 2, 10),
			exemption_to=date(2026, 2, 28),
			exemption_reason="Інше (вказати)",
		)
		esv = rows_of_type(build_deadline_rows(2026, "2", AMOUNTS, exempt), "ЄСВ")

		self.assertEqual(len(esv), 4)
		self.assertEqual(esv[0].amount, 3804.68)
		self.assertIn("січень 2026, березень 2026", esv[0].notes)
		self.assertEqual([row.amount for row in esv[1:]], [5707.02] * 3)

	def test_confirmed_fop_amount_overrides_the_yearly_minimum(self):
		above = ESVContext(mode=ESV_MODE_ABOVE_MINIMUM, monthly_override=2500.0)
		esv = rows_of_type(build_deadline_rows(2026, "3", AMOUNTS, above), "ЄСВ")

		self.assertEqual([row.amount for row in esv], [7500.0] * 4)
		self.assertIn("за підтвердженою сумою ЄСВ цього ФОП", esv[0].notes)

	def test_override_is_prorated_together_with_a_mid_year_exemption(self):
		context = ESVContext(
			mode=ESV_MODE_EXEMPT,
			monthly_override=2500.0,
			exemption_from=date(2026, 12, 1),
			exemption_reason="Пенсіонер за віком",
		)
		esv = rows_of_type(build_deadline_rows(2026, "2", AMOUNTS, context), "ЄСВ")

		self.assertEqual([row.amount for row in esv], [7500.0, 7500.0, 7500.0, 5000.0])

	def test_exemption_outside_the_calendar_year_changes_nothing(self):
		context = ESVContext(
			mode=ESV_MODE_EXEMPT,
			exemption_from=date(2027, 1, 1),
			exemption_reason="Пенсіонер за віком",
		)
		esv = rows_of_type(build_deadline_rows(2026, "2", AMOUNTS, context), "ЄСВ")

		self.assertEqual([row.amount for row in esv], [5707.02] * 4)

	def test_exemption_without_a_start_month_does_not_silently_drop_obligations(self):
		context = ESVContext(mode=ESV_MODE_EXEMPT, exemption_reason="Пенсіонер за віком")
		esv = rows_of_type(build_deadline_rows(2026, "2", AMOUNTS, context), "ЄСВ")

		self.assertEqual(len(esv), 4)

	def test_esv_period_labels_cover_the_whole_year(self):
		self.assertEqual(
			esv_period_labels(2026),
			("1 квартал 2026", "2 квартал 2026", "3 квартал 2026", "4 квартал 2026"),
		)
		generated = {
			row.period_label
			for row in build_deadline_rows(
				2026,
				"2",
				AMOUNTS,
				ESVContext(
					mode=ESV_MODE_EXEMPT,
					exemption_from=date(2026, 7, 1),
					exemption_reason="Пенсіонер за віком",
				),
			)
			if row.tax_type == "ЄСВ"
		}
		superseded = [label for label in esv_period_labels(2026) if label not in generated]
		self.assertEqual(superseded, ["3 квартал 2026", "4 квартал 2026"])

	def test_building_rows_twice_is_deterministic(self):
		context = ESVContext(
			mode=ESV_MODE_EXEMPT,
			exemption_from=date(2026, 5, 1),
			exemption_reason="Пенсіонер за віком",
		)
		first = build_deadline_rows(2026, "1", AMOUNTS, context)
		second = build_deadline_rows(2026, "1", AMOUNTS, context)

		self.assertEqual(first, second)

	def test_only_official_https_sources_are_accepted(self):
		sources = official_source_urls(
			"https://tax.gov.ua/rule\n\nhttps://example.gov.ua/local-decision"
		)
		self.assertEqual(len(sources), 2)
		self.assertTrue(all(is_official_source(source) for source in sources))
		self.assertFalse(is_official_source("http://tax.gov.ua/insecure"))
		self.assertFalse(is_official_source("https://example.com/not-official"))


if __name__ == "__main__":
	unittest.main()
