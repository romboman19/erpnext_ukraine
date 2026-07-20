from types import SimpleNamespace
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.setup.test_site import (
    COMPANY,
    CONFIRMATION,
    _assert_fields,
    _assert_scope,
)


class TestSiteBootstrapSafetyTests(TestCase):
    def test_scope_accepts_only_exact_test_site_token_and_company(self) -> None:
        frappe = SimpleNamespace(local=SimpleNamespace(site="postest.local"))
        _assert_scope(
            frappe,
            confirm_site="postest.local",
            confirm_write=CONFIRMATION,
            company=COMPANY,
        )

    def test_scope_rejects_restore_or_production_sites(self) -> None:
        for site in ("postest-restore.local", "erp.huntervua.pp.ua"):
            with self.subTest(site=site), self.assertRaisesRegex(RuntimeError, "restricted"):
                _assert_scope(
                    SimpleNamespace(local=SimpleNamespace(site=site)),
                    confirm_site=site,
                    confirm_write=CONFIRMATION,
                    company=COMPANY,
                )

    def test_scope_rejects_wrong_confirmation_or_company(self) -> None:
        frappe = SimpleNamespace(local=SimpleNamespace(site="postest.local"))
        with self.assertRaisesRegex(RuntimeError, "confirmation"):
            _assert_scope(frappe, confirm_site="postest.local", confirm_write="wrong", company=COMPANY)
        with self.assertRaisesRegex(RuntimeError, "Company"):
            _assert_scope(
                frappe,
                confirm_site="postest.local",
                confirm_write=CONFIRMATION,
                company="Another Company",
            )

    def test_existing_field_check_treats_erpnext_numeric_normalization_as_equal(self) -> None:
        document = {"commission_rate": 15.0, "enabled": 0}
        _assert_fields(document, {"commission_rate": 15, "enabled": False}, "fixture")
