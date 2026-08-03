import json
import unittest
from pathlib import Path

from erpnext_ua.integrations.shipment.profile_selection import select_sender_profile

APP = Path(__file__).resolve().parents[1]


class TestShipmentProfileSelection(unittest.TestCase):
    def test_selects_default_for_document_company(self):
        profiles = [
            {"name": "FOP A", "company": "FOP A LLC", "default": True},
            {"name": "FOP B", "company": "FOP B LLC", "default": True},
        ]

        selected = select_sender_profile(
            profiles,
            carrier="Nova Poshta",
            company="FOP B LLC",
        )

        self.assertEqual(selected["name"], "FOP B")

    def test_rejects_explicit_profile_from_another_company(self):
        profiles = [{"name": "FOP A", "company": "FOP A LLC", "default": True}]

        with self.assertRaisesRegex(ValueError, "different company"):
            select_sender_profile(
                profiles,
                carrier="Ukrposhta",
                requested="FOP A",
                company="FOP B LLC",
            )

    def test_rejects_legacy_unbound_profile_for_business_document(self):
        profiles = [{"name": "Legacy", "company": "", "default": True}]

        with self.assertRaisesRegex(ValueError, "different company"):
            select_sender_profile(
                profiles,
                carrier="Nova Poshta",
                requested="Legacy",
                company="FOP A LLC",
            )

    def test_requires_explicit_choice_when_defaults_are_ambiguous(self):
        profiles = [
            {"name": "First", "company": "FOP A LLC", "default": False},
            {"name": "Second", "company": "FOP A LLC", "default": False},
        ]

        with self.assertRaisesRegex(ValueError, "explicitly"):
            select_sender_profile(
                profiles,
                carrier="Ukrposhta",
                company="FOP A LLC",
            )

    def test_sender_profile_doctypes_require_company(self):
        for path in (
            APP
            / "ukrainian_integrations"
            / "doctype"
            / "np_sender_profile"
            / "np_sender_profile.json",
            APP
            / "ukrainian_integrations"
            / "doctype"
            / "up_sender_profile"
            / "up_sender_profile.json",
        ):
            definition = json.loads(path.read_text(encoding="utf-8"))
            company = next(row for row in definition["fields"] if row["fieldname"] == "company")
            self.assertEqual(company["options"], "Company")
            self.assertEqual(company["reqd"], 1)

    def test_sales_invoice_ui_filters_profiles_by_company(self):
        source = (APP / "public" / "js" / "sales_invoice_shipment_actions.js").read_text(
            encoding="utf-8"
        )

        # Nova Poshta, Ukrposhta, and the existing Rozetka flow all scope profiles.
        self.assertEqual(source.count("args: { company: frm.doc.company }"), 3)


if __name__ == "__main__":
    unittest.main()
