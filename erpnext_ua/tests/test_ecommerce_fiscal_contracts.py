import json
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1]


class TestEcommerceFiscalContracts(unittest.TestCase):
    def test_payment_entry_hooks_fiscalize_and_protect_paid_online_invoice(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        service = (APP / "ua_fiscal" / "ecommerce.py").read_text(encoding="utf-8")

        self.assertIn("ua_fiscal.ecommerce.on_payment_submit", hooks)
        self.assertIn("ua_fiscal.ecommerce.before_payment_cancel", hooks)
        self.assertIn("outstanding_amount", service)
        self.assertIn("fiscalize_invoice(sales_invoice)", service)
        self.assertIn("recover_pending_ecommerce_receipts", hooks)
        self.assertIn("ua_ecommerce_fiscal_status", service)
        self.assertIn('PROTECTED_RECEIPT_STATUSES = (*COMPLETED_RECEIPT_STATUSES, "Uncertain")', service)
        self.assertIn("Create a return Sales Invoice", service)

    def test_ecommerce_register_is_explicit_and_company_scoped(self):
        definition = json.loads(
            (
                APP
                / "ua_fiscal"
                / "doctype"
                / "prro_cash_register"
                / "prro_cash_register.json"
            ).read_text(encoding="utf-8")
        )
        register = (APP / "ua_fiscal" / "sales_invoice.py").read_text(encoding="utf-8")
        fields = {row["fieldname"]: row for row in definition["fields"]}

        self.assertIn("ecommerce_default", fields)
        self.assertEqual(fields["ecommerce_default"]["default"], "0")
        self.assertIn('"company": si.company', register)
        self.assertIn('"ecommerce_default": 1', register)

    def test_ecommerce_payment_never_falls_back_to_cash(self):
        source = (APP / "ua_fiscal" / "sales_invoice.py").read_text(encoding="utf-8")
        ecommerce_guard = source.index('if not payments and si.get("ua_ecommerce_channel")')
        legacy_fallback = source.index("legacy non-POS manual fiscalization")

        self.assertLess(ecommerce_guard, legacy_fallback)
        self.assertIn("_submitted_payment_rows", source)


if __name__ == "__main__":
    unittest.main()
