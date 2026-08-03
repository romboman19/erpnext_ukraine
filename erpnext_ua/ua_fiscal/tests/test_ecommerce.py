from __future__ import annotations

from unittest.mock import patch

import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from frappe.tests import IntegrationTestCase

from erpnext_ua.tests.integrations.frappe_fixtures import (
    ensure_company_fiscal_year,
    ensure_customer_master_links,
    ensure_leaf_master,
    ensure_selling_price_list,
)
from erpnext_ua.ua_fiscal import outbox
from erpnext_ua.ua_fiscal.sales_invoice import _invoice_payments


class TestEcommerceFiscalization(IntegrationTestCase):
    def setUp(self):
        super().setUp()
        frappe.set_user("Administrator")
        self.suffix = frappe.generate_hash(length=8).upper()
        self.company = self._company()
        ensure_company_fiscal_year(
            self.company.name,
            f"_UA Ecommerce Fiscal Year {self.suffix}",
        )
        self.price_list = ensure_selling_price_list(
            f"_UA Ecommerce Selling {self.suffix}",
            "UAH",
        )
        self.customer = self._customer()
        self.item = self._item()
        self.bank_account = self._bank_account()
        self.payment_mode = self._payment_mode()
        self.register = self._register()
        self._enable_test_prro()

    def test_payment_submit_uses_updated_outstanding_and_explicit_payment_mode(self):
        invoice = self._ecommerce_invoice()
        payment = self._payment_entry(invoice, self.payment_mode)
        observed = {}

        def fiscalize_without_external_prro(sales_invoice, **_kwargs):
            current_invoice = frappe.get_doc("Sales Invoice", sales_invoice)
            observed["outstanding_amount"] = current_invoice.outstanding_amount
            observed["payments"] = _invoice_payments(current_invoice)
            return self._mock_fiscal_receipt(current_invoice)

        with patch.object(
            outbox,
            "fiscalize_invoice",
            side_effect=fiscalize_without_external_prro,
        ) as fiscalize:
            payment.submit()

        invoice.reload()
        payment.reload()
        self.assertEqual(payment.docstatus, 1)
        self.assertEqual(payment.mode_of_payment, self.payment_mode)
        self.assertAlmostEqual(observed["outstanding_amount"], 0, places=2)
        self.assertEqual(
            observed["payments"],
            [
                {
                    "code": 1,
                    "name": "ТЕСТОВА КАРТКА",
                    "form": "БЕЗГОТІВКОВА",
                    "sum": invoice.grand_total,
                }
            ],
        )
        self.assertEqual(invoice.outstanding_amount, 0)
        self.assertEqual(invoice.ua_ecommerce_fiscal_status, "Fiscalized")
        self.assertEqual(invoice.ua_ecommerce_fiscal_error, "")
        self.assertEqual(fiscalize.call_args.args[0], invoice.name)
        job = frappe.get_doc("PRRO Fiscalization Job", {"sales_invoice": invoice.name})
        self.assertEqual(job.status, "Completed")
        self.assertTrue(job.receipt)

        self._assert_missing_payment_mode_fails_closed()

    def _assert_missing_payment_mode_fails_closed(self):
        invoice = self._ecommerce_invoice()
        payment = self._payment_entry(invoice, "")

        def validate_payments_without_external_prro(sales_invoice, **_kwargs):
            _invoice_payments(frappe.get_doc("Sales Invoice", sales_invoice))
            self.fail("Payment without Mode of Payment reached PRRO")

        with patch.object(
            outbox,
            "fiscalize_invoice",
            side_effect=validate_payments_without_external_prro,
        ):
            payment.submit()

        invoice.reload()
        self.assertEqual(invoice.outstanding_amount, 0)
        self.assertEqual(invoice.ua_ecommerce_fiscal_status, "Error")
        self.assertIn("не вказано спосіб оплати", invoice.ua_ecommerce_fiscal_error)
        self.assertFalse(frappe.db.exists("PRRO Receipt", {"sales_invoice": invoice.name}))
        job = frappe.get_doc("PRRO Fiscalization Job", {"sales_invoice": invoice.name})
        self.assertEqual(job.status, "Pending")
        self.assertGreater(job.attempt_count, 0)

    def _company(self):
        if not frappe.db.exists("Warehouse Type", "Transit"):
            frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(ignore_permissions=True)
        return frappe.get_doc(
            {
                "doctype": "Company",
                "company_name": f"_UA Ecommerce Fiscal {self.suffix}",
                "abbr": f"E{self.suffix[:4]}",
                "country": "Ukraine",
                "default_currency": "UAH",
                "create_chart_of_accounts_based_on": "Standard Template",
                "chart_of_accounts": "Standard",
            }
        ).insert(ignore_permissions=True)

    def _customer(self):
        customer_group, territory = ensure_customer_master_links()
        return frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": f"_UA Ecommerce Customer {self.suffix}",
                "customer_type": "Individual",
                "customer_group": customer_group,
                "territory": territory,
            }
        ).insert(ignore_permissions=True)

    def _item(self):
        if not frappe.db.exists("UOM", "Nos"):
            frappe.get_doc(
                {
                    "doctype": "UOM",
                    "uom_name": "Nos",
                    "must_be_whole_number": 1,
                }
            ).insert(ignore_permissions=True)
        item_group = ensure_leaf_master(
            doctype="Item Group",
            name=f"_UA Ecommerce Items {self.suffix}",
            name_field="item_group_name",
            parent_field="parent_item_group",
        )
        return frappe.get_doc(
            {
                "doctype": "Item",
                "item_code": f"UA-ECOM-{self.suffix}",
                "item_name": f"UA Ecommerce Service {self.suffix}",
                "item_group": item_group,
                "stock_uom": "Nos",
                "is_stock_item": 0,
                "is_sales_item": 1,
            }
        ).insert(ignore_permissions=True)

    def _bank_account(self):
        account = frappe.db.get_value(
            "Account",
            {
                "company": self.company.name,
                "account_type": "Bank",
                "is_group": 0,
                "disabled": 0,
            },
            "name",
        )
        if account:
            return account

        asset_root = frappe.db.get_value(
            "Account",
            {
                "company": self.company.name,
                "root_type": "Asset",
                "is_group": 1,
            },
            "name",
            order_by="lft asc",
        )
        self.assertTrue(asset_root, "Company fixture did not create an asset root")
        bank_group = frappe.get_doc(
            {
                "doctype": "Account",
                "account_name": f"Ecommerce Banks {self.suffix}",
                "company": self.company.name,
                "parent_account": asset_root,
                "root_type": "Asset",
                "is_group": 1,
            }
        ).insert(ignore_permissions=True)
        return (
            frappe.get_doc(
                {
                    "doctype": "Account",
                    "account_name": f"Ecommerce Bank {self.suffix}",
                    "company": self.company.name,
                    "parent_account": bank_group.name,
                    "root_type": "Asset",
                    "account_type": "Bank",
                    "account_currency": "UAH",
                    "is_group": 0,
                }
            )
            .insert(ignore_permissions=True)
            .name
        )

    def _payment_mode(self):
        payment_mode = frappe.get_doc(
            {
                "doctype": "Mode of Payment",
                "mode_of_payment": f"UA Ecommerce Card {self.suffix}",
                "type": "Bank",
                "ua_payformcd": "1",
                "ua_prro_payment_form": "БЕЗГОТІВКОВА",
                "ua_prro_payment_means": "ТЕСТОВА КАРТКА",
                "accounts": [
                    {
                        "company": self.company.name,
                        "default_account": self.bank_account,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        return payment_mode.name

    def _enable_test_prro(self):
        settings = frappe.get_single("PRRO Settings")
        settings.update(
            {
                "enabled": 1,
                "mode": "Тестовий",
                "fiscal_server_url": "https://example.invalid/fs",
                "signservice_url": "http://signer.invalid",
                "signservice_api_key": "integration-test-only",
            }
        )
        settings.save(ignore_permissions=True)
        frappe.clear_document_cache("PRRO Settings")

    def _register(self):
        base = f"{int(self.suffix, 36) % 1_000_000_000:09d}"
        weights = (-1, 5, 7, 9, 4, 6, 10, 5, 7)
        control = sum(int(digit) * weight for digit, weight in zip(base, weights)) % 11 % 10
        profile = frappe.get_doc(
            {
                "doctype": "FOP Profile",
                "company": self.company.name,
                "fop_full_name": f"Test FOP {self.suffix}",
                "prro_registered_name": f"Test FOP {self.suffix}",
                "tax_id": f"{base}{control}",
                "single_tax_group": "3",
                "tax_rate_mode": "5% без ПДВ",
                "allow_manual_dps_fields": 1,
            }
        ).insert(ignore_permissions=True)
        return frappe.get_doc(
            {
                "doctype": "PRRO Cash Register",
                "register_name": f"_Test Ecommerce Register {self.suffix}",
                "fop_profile": profile.name,
                "status": "Active",
                "fiscal_number": f"4{int(self.suffix, 36) % 10**11:011d}",
                "register_local_number": 1,
                "unit_name": "Integration Test Ecommerce",
                "unit_address": "Test address",
                "ecommerce_default": 1,
            }
        ).insert(ignore_permissions=True)

    def _ecommerce_invoice(self):
        invoice = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": self.company.name,
                "customer": self.customer.name,
                "currency": "UAH",
                "selling_price_list": self.price_list,
                "price_list_currency": "UAH",
                "plc_conversion_rate": 1,
                "is_pos": 0,
                "update_stock": 0,
                "ua_ecommerce_channel": "integration-test",
                "ua_ecommerce_fiscal_status": "Pending",
                "items": [
                    {
                        "item_code": self.item.name,
                        "qty": 1,
                        "uom": "Nos",
                        "rate": 100,
                        "price_list_rate": 100,
                    }
                ],
            }
        )
        invoice.set_missing_values()
        invoice.insert(ignore_permissions=True)
        invoice.submit()
        invoice.reload()
        self.assertGreater(invoice.outstanding_amount, 0)
        return invoice

    def _payment_entry(self, invoice, mode_of_payment):
        payment = get_payment_entry(
            "Sales Invoice",
            invoice.name,
            bank_account=self.bank_account,
        )
        payment.mode_of_payment = mode_of_payment
        payment.reference_no = f"ECOM-{frappe.generate_hash(length=10)}"
        payment.reference_date = frappe.utils.today()
        payment.insert(ignore_permissions=True)
        return payment

    def _mock_fiscal_receipt(self, invoice):
        receipt = frappe.get_doc(
            {
                "doctype": "PRRO Receipt",
                "cash_register": self.register.name,
                "shift": "_Test Ecommerce Shift",
                "receipt_type": "Продаж",
                "receipt_kind": "Sale",
                "status": "Fiscalized",
                "sales_invoice": invoice.name,
                "idem_key": f"test:ecommerce:{invoice.name}",
                "total_amount": invoice.grand_total,
            }
        )
        receipt.flags.ignore_links = True
        receipt.insert(ignore_permissions=True)
        return receipt.name
