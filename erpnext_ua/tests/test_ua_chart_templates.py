import json
import unittest
from pathlib import Path

from erpnext_ua.ua_accounting.chart_of_accounts.templates import (
	REQUIRED_ACCOUNT_TYPES,
	TEMPLATE_FILES,
)
from erpnext_ua.ua_accounting.chart_of_accounts import (
	build_erpnext_tree,
	load_template,
	template_summary,
)


SIMPLIFIED_OFFICIAL_CODES = {
	"10", "13", "14", "15", "16", "18", "20", "21", "23", "26",
	"30", "31", "35", "37", "39", "40", "44", "47", "48", "55",
	"64", "66", "68", "69", "70", "74", "79", "90", "91", "97",
}


def flatten_tree(tree):
	rows = {}

	def visit(node):
		for name, child in node.items():
			if not isinstance(child, dict):
				continue
			if child.get("account_number"):
				rows[child["account_number"]] = (name, child)
			visit(child)

	visit(tree)
	return rows


class TestUAChartTemplates(unittest.TestCase):
	def test_public_workspace_has_matching_sidebar_and_off_balance_links(self):
		app_path = Path(__file__).resolve().parents[1]
		workspace = json.loads(
			(app_path / "ua_accounting/workspace/ua_accounting/ua_accounting.json").read_text(encoding="utf-8")
		)
		sidebar = json.loads((app_path / "workspace_sidebar/ua_accounting.json").read_text(encoding="utf-8"))

		self.assertTrue(workspace["public"])
		self.assertEqual(sidebar["name"], workspace["name"])
		self.assertEqual(sidebar["title"], workspace["title"])
		self.assertEqual(sidebar["module"], workspace["module"])
		workspace_targets = {row.get("link_to") for row in workspace["links"]}
		sidebar_targets = {row.get("link_to") for row in sidebar["items"]}
		for target in ("UA Off Balance Entry", "UA Off Balance Statement"):
			self.assertIn(target, workspace_targets)
			self.assertIn(target, sidebar_targets)

	def test_full_plan_is_complete_and_current(self):
		template = load_template("full_291")
		summary = template_summary(template)
		codes = {row["code"] for row in template["accounts"]}

		self.assertEqual(summary["official_account_count"], 354)
		self.assertEqual(summary["erpnext_extension_count"], 10)
		self.assertTrue({"308", "335", "676", "686"}.issubset(codes))
		self.assertTrue({"01", "09", "024", "100", "283", "685", "703", "976"}.issubset(codes))
		self.assertFalse({"75", "85", "99"} & codes)
		self.assertEqual(len(codes), len(template["accounts"]))

	def test_simplified_plan_contains_exact_official_synthetics_and_class_zero(self):
		template = load_template("simplified_186")
		official = [row for row in template["accounts"] if row["source"] == "official"]
		official_synthetic = {row["code"] for row in official if len(row["code"]) == 2 and not row["code"].startswith("0")}

		self.assertEqual(official_synthetic, SIMPLIFIED_OFFICIAL_CODES)
		self.assertIn("48", official_synthetic)
		self.assertFalse({"84", "85"} & official_synthetic)
		self.assertTrue(all(any(row["code"] == f"0{i}" for row in official) for i in range(1, 10)))
		self.assertTrue(any(row["code"] == "024" for row in official))
		self.assertEqual(template_summary(template)["erpnext_extension_count"], 19)

	def test_templates_supply_all_erpnext_account_types_and_defaults(self):
		for key in TEMPLATE_FILES:
			with self.subTest(template=key):
				template = load_template(key)
				account_types = {row.get("account_type") for row in template["accounts"]}
				self.assertTrue(REQUIRED_ACCOUNT_TYPES.issubset(account_types))
				tree_rows = flatten_tree(build_erpnext_tree(template))
				for fieldname, code in template["defaults"].items():
					self.assertIn(code, tree_rows, fieldname)
					self.assertFalse(tree_rows[code][1].get("is_group"), fieldname)

	def test_templates_identify_official_and_operational_sources(self):
		for key in TEMPLATE_FILES:
			with self.subTest(template=key):
				template = load_template(key)
				self.assertTrue(all(row["source"] in {"official", "erpnext_extension"} for row in template["accounts"]))
				self.assertTrue(any(row["source"] == "erpnext_extension" for row in template["accounts"]))
				self.assertTrue(all(item["url"].startswith("https://zakon.rada.gov.ua/") for item in template["legal_basis"]))


if __name__ == "__main__":
	unittest.main()
