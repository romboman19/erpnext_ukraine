from __future__ import annotations

import json
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1]


class TestPOSIntegrationContracts(unittest.TestCase):
    def test_pos_uses_policy_aware_identification_endpoint(self):
        source = (APP / "ua_pos" / "page" / "ua_pos" / "ua_pos.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('identificationApi("begin_pos"', source)
        self.assertIn("config.pos_channel", source)
        self.assertIn("config.allow_pos_channel_selection", source)
        self.assertNotIn('identificationApi("begin",', source)

    def test_pos_workspace_is_visible_and_opens_the_cashier_page(self):
        workspace = json.loads(
            (
                APP
                / "ua_pos"
                / "workspace"
                / "ua_pos"
                / "ua_pos.json"
            ).read_text(encoding="utf-8")
        )
        self.assertTrue(workspace["public"])
        self.assertFalse(workspace["is_hidden"])
        self.assertEqual(workspace["name"], "UA POS Workspace")
        self.assertTrue(
            any(
                link.get("link_type") == "Page"
                and link.get("link_to") == "ua-pos"
                for link in workspace["links"]
            )
        )

        icon = json.loads(
            (APP / "desktop_icon" / "ua_pos.json").read_text(encoding="utf-8")
        )
        self.assertEqual(icon["parent_icon"], "ERPNext Ukraine")
        self.assertFalse(icon["hidden"])


if __name__ == "__main__":
    unittest.main()
