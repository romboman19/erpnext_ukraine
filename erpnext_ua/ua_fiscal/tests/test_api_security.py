from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe

from erpnext_ua.ua_fiscal import api, authorization


def _invoice(**overrides):
    values = {
        "doctype": "Sales Invoice",
        "name": "SINV-SECURITY-TEST",
        "docstatus": 1,
        "is_pos": 1,
        "ua_ecommerce_channel": None,
    }
    values.update(overrides)
    return frappe._dict(values)


class TestPRROAuthorization(unittest.TestCase):
    @patch.object(authorization.frappe, "has_permission", return_value=True)
    @patch.object(authorization.frappe, "get_roles", return_value=["Guest"])
    def test_unprivileged_user_cannot_fiscalize_invoice(self, _get_roles, _has_permission):
        with self.assertRaises(frappe.PermissionError):
            authorization.require_sales_invoice_fiscalization(_invoice())

    @patch.object(authorization.frappe, "has_permission", return_value=False)
    @patch.object(authorization.frappe, "get_roles", return_value=["Accounts User"])
    def test_accounts_user_cannot_cross_document_permissions(self, _get_roles, _has_permission):
        with self.assertRaises(frappe.PermissionError):
            authorization.require_sales_invoice_fiscalization(_invoice())

    @patch.object(authorization.frappe, "has_permission", return_value=True)
    @patch.object(authorization.frappe, "get_roles", return_value=["Accounts User"])
    def test_draft_and_non_fiscal_invoices_are_rejected(self, _get_roles, _has_permission):
        with self.assertRaises(frappe.ValidationError):
            authorization.require_sales_invoice_fiscalization(_invoice(docstatus=0))
        with self.assertRaises(frappe.ValidationError):
            authorization.require_sales_invoice_fiscalization(_invoice(is_pos=0, ua_ecommerce_channel=None))

    @patch.object(api, "fiscalize_invoice")
    @patch.object(api, "require_sales_invoice_fiscalization")
    @patch.object(api.frappe, "get_doc")
    def test_denied_request_never_reaches_orchestration(self, get_doc, authorize, fiscalize):
        get_doc.return_value = _invoice()
        authorize.side_effect = frappe.PermissionError("denied")

        with self.assertRaises(frappe.PermissionError):
            api.fiscalize_sales_invoice("SINV-SECURITY-TEST")

        fiscalize.assert_not_called()

    @patch.object(api.orchestration, "register_device", return_value={"ok": True})
    @patch.object(api, "require_roles")
    @patch.object(api, "require_register_control")
    @patch.object(api.frappe, "get_doc")
    def test_forced_device_registration_requires_system_manager(
        self,
        get_doc,
        _require_register_control,
        require_roles,
        register_device,
    ):
        get_doc.return_value = frappe._dict(
            {
                "doctype": "PRRO Cash Register",
                "name": "REGISTER-SECURITY-TEST",
                "default_kep_key": "KEP-SECURITY-TEST",
            }
        )

        api.register_device("REGISTER-SECURITY-TEST", forced=True)

        require_roles.assert_called_once_with(("System Manager",))
        register_device.assert_called_once_with(
            "REGISTER-SECURITY-TEST",
            "KEP-SECURITY-TEST",
            forced=True,
        )


if __name__ == "__main__":
    unittest.main()
