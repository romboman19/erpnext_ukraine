import frappe
from frappe.tests import IntegrationTestCase

from erpnext_ua.ua_fop.tax_rules import missing_parameter_fields


class TestTaxParameterSeed(IntegrationTestCase):
	def test_2026_seed_is_complete_and_uses_the_official_group_one_maximum(self):
		for group in ("1", "2", "3"):
			params = self._params(group)
			self.assertEqual(missing_parameter_fields(group, params.as_dict()), ())
			self.assertEqual(str(params.verified_on), "2026-08-03")
			self.assertIn("tax.gov.ua", params.official_sources)

		group_one = self._params("1")
		self.assertEqual(group_one.subsistence_minimum, 3328)
		self.assertEqual(group_one.single_tax_monthly, 332.80)

	def _params(self, group: str):
		name = frappe.db.get_value(
			"UA Tax Parameters", {"year": 2026, "single_tax_group": group}
		)
		return frappe.get_doc("UA Tax Parameters", name)
