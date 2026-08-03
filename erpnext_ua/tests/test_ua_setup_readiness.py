from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from erpnext_ua.ua_setup.readiness import (
	SetupState,
	Severity,
	Status,
	blocking,
	can_fiscalize,
	can_sell,
	evaluate,
)

CLEAN_SITE = SetupState(current_year=2026)

READY_TO_SELL = SetupState(
	company="ФОП Тест",
	company_country="Ukraine",
	company_currency="UAH",
	system_language="uk",
	chart_template="full_291",
	tax_parameter_years=frozenset({2026}),
	tax_parameter_groups=frozenset({"1", "2", "3"}),
	current_year=2026,
	active_fop_profiles=1,
	fop_has_kved=True,
	warehouses=1,
	retail_customers=1,
	active_cash_desks=1,
	pos_cash_payment_methods=1,
	pos_cashless_payment_methods=1,
	print_formats=7,
)

READY_TO_FISCALIZE = replace(
	READY_TO_SELL,
	prro_registers=1,
	prro_registers_with_device_id=1,
	kep_keys=1,
	prro_signer_configured=True,
)


def check_for(state: SetupState, step: str):
	return next(check for check in evaluate(state) if check.step == step)


class TestSetupReadiness(unittest.TestCase):
	def test_clean_site_cannot_sell_and_names_every_missing_step(self):
		checks = evaluate(CLEAN_SITE)

		self.assertFalse(can_sell(checks))
		self.assertFalse(can_fiscalize(checks))
		self.assertEqual(
			[check.step for check in blocking(checks)],
			[
				"company",
				"chart",
				"tax_parameters",
				"fop_profile",
				"warehouse",
				"retail_customer",
				"cash_desk",
				"payment_methods",
			],
		)

	def test_every_check_explains_itself(self):
		for check in evaluate(CLEAN_SITE):
			with self.subTest(step=check.step):
				self.assertTrue(check.title)
				self.assertTrue(check.detail, "a pending step has to say what is missing")

	def test_configured_site_can_sell_but_not_fiscalize_without_prro(self):
		checks = evaluate(READY_TO_SELL)

		self.assertTrue(can_sell(checks))
		self.assertFalse(can_fiscalize(checks))
		unmet = {check.step for check in checks if check.status is not Status.DONE}
		self.assertEqual(unmet, {"prro_register", "prro_signing"})

	def test_fully_configured_site_is_ready(self):
		checks = evaluate(READY_TO_FISCALIZE)

		self.assertTrue(can_fiscalize(checks))
		self.assertTrue(all(check.status is Status.DONE for check in checks))

	def test_wrong_country_or_currency_is_reported_not_silently_accepted(self):
		state = replace(READY_TO_SELL, company_country="Poland", company_currency="PLN")

		check = check_for(state, "company")
		self.assertIs(check.status, Status.PENDING)
		self.assertIn("Ukraine", check.detail)
		self.assertIn("UAH", check.detail)

	def test_company_with_postings_blocks_instead_of_offering_a_fix(self):
		state = replace(
			READY_TO_SELL,
			company_currency="PLN",
			chart_template="",
			company_has_gl_entries=True,
		)

		company = check_for(state, "company")
		chart = check_for(state, "chart")
		self.assertIs(company.status, Status.BLOCKED)
		self.assertIs(chart.status, Status.BLOCKED)
		self.assertEqual(chart.fix_action, "", "a company with postings must not offer a chart replacement")

	def test_tax_parameters_are_checked_for_the_current_year(self):
		state = replace(
			READY_TO_SELL,
			tax_parameter_years=frozenset({2025}),
			tax_parameter_groups=frozenset(),
			current_year=2026,
		)

		check = check_for(state, "tax_parameters")
		self.assertIs(check.status, Status.PENDING)
		self.assertIn("2026", check.detail)

	def test_tax_parameters_require_every_supported_group(self):
		state = replace(READY_TO_SELL, tax_parameter_groups=frozenset({"1", "3"}))

		check = check_for(state, "tax_parameters")
		self.assertIs(check.status, Status.PENDING)
		self.assertIn("2", check.detail)

	def test_cash_only_payment_setup_is_not_enough(self):
		state = replace(READY_TO_SELL, pos_cashless_payment_methods=0)

		check = check_for(state, "payment_methods")
		self.assertIs(check.status, Status.PENDING)
		self.assertIn("безготівкова", check.detail)

	def test_language_and_kved_never_block_selling(self):
		state = replace(READY_TO_SELL, system_language="en", fop_has_kved=False)

		checks = evaluate(state)
		self.assertTrue(can_sell(checks))
		for step in ("language", "kved"):
			self.assertIs(check_for(state, step).severity, Severity.RECOMMENDED)

	def test_prro_register_without_device_id_stays_pending(self):
		state = replace(READY_TO_FISCALIZE, prro_registers=2, prro_registers_with_device_id=1)

		check = check_for(state, "prro_register")
		self.assertIs(check.status, Status.PENDING)
		self.assertFalse(can_fiscalize(evaluate(state)))

	def test_every_fix_action_is_wired_to_a_step_or_a_navigation_target(self):
		# Read both controllers as text: importing them needs Frappe, and this is
		# a contract between files, not runtime behaviour. A fix_action either
		# runs server-side (_step_<action> in the .py, for actions with no
		# dedicated DocType of their own) or routes to a real form that already
		# exists for that data (NAVIGATE_ACTIONS in the .js) — never both, and
		# never neither.
		wizard_dir = Path(__file__).resolve().parents[1] / "ua_setup" / "doctype" / "ua_setup_wizard"
		wizard_py = (wizard_dir / "ua_setup_wizard.py").read_text(encoding="utf-8")
		wizard_js = (wizard_dir / "ua_setup_wizard.js").read_text(encoding="utf-8")

		for check in evaluate(CLEAN_SITE) + evaluate(READY_TO_SELL):
			if not check.fix_action:
				continue
			with self.subTest(step=check.step):
				has_step = f"def _step_{check.fix_action}(" in wizard_py
				has_navigation = f"{check.fix_action}: {{" in wizard_js
				self.assertTrue(
					has_step or has_navigation,
					f"{check.fix_action} has neither a wizard step nor a navigation target",
				)
				self.assertFalse(
					has_step and has_navigation,
					f"{check.fix_action} is wired both as a step and as navigation",
				)


if __name__ == "__main__":
	unittest.main()
