"""Clean-site consignment price-version interval and rollback lifecycle."""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, get_datetime, nowdate

from erpnext_ua.consignment_and_commission.integrations.pricing import (
    get_effective_price_version,
)

from .test_frappe_foundation import COMPANY, LOCATION, PARTNER, _cleanup_integration_records
from .test_frappe_own_receipt import _cleanup_own_receipts
from .test_frappe_receipt import (
    _cleanup_receipt_records,
    _ensure_item,
    _ensure_receipt_context,
)


def _cleanup_price_versions() -> None:
    if not frappe.db.exists("DocType", "CC Price Version"):
        return
    while True:
        active = frappe.db.get_value(
            "CC Price Version",
            {"company": COMPANY, "docstatus": 1, "status": "ACTIVE"},
            "name",
            order_by="valid_from desc",
        )
        if not active:
            break
        frappe.get_doc("CC Price Version", active).cancel()
    for name in frappe.get_all("CC Price Version", filters={"company": COMPANY}, pluck="name"):
        frappe.delete_doc("CC Price Version", name, force=True, ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache()


class TestFrappePricing(IntegrationTestCase):
    def test_consignment_price_versions_are_effective_and_reversible(self) -> None:
        _cleanup_price_versions()
        _cleanup_own_receipts()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_own_receipts)
        self.addCleanup(_cleanup_price_versions)

        company, _warehouses, _commission_contract = _ensure_receipt_context()
        item_code = _ensure_item()
        contract = frappe.get_doc(
            {
                "doctype": "CC Contract",
                "contract_title": "_CC Integration Consignment Contract",
                "status": "ACTIVE",
                "partner_profile": PARTNER,
                "company": COMPANY,
                "location": LOCATION,
                "relationship_model": "CONSIGNMENT",
                "currency": company.default_currency,
                "commission_rate": 0,
                "valid_from": nowdate(),
                "settlement_frequency": "MONTHLY",
                "settlement_deadline_days": 7,
                "fiscal_policy": "AUTO",
                "price_authority": "CONTRACT",
            }
        ).insert(ignore_permissions=True)
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": "00:10:00",
                "contract": contract.name,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "accounting_unit_value": 100,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()
        lot = frappe.get_doc("CC Stock Lot", receipt.items[0].stock_lot)
        first_start = get_datetime(lot.received_datetime)
        second_start = add_to_date(first_start, minutes=10, as_datetime=True)

        first = frappe.get_doc(
            {
                "doctype": "CC Price Version",
                "stock_lot": lot.name,
                "partner_rate": 70,
                "valid_from": first_start,
                "notes": "Initial agreed consignment rate",
            }
        ).insert(ignore_permissions=True)
        first.submit()
        self.assertEqual(first.status, "ACTIVE")
        self.assertEqual(first.currency, company.default_currency)

        with self.assertRaisesRegex(frappe.ValidationError, "already starts"):
            frappe.get_doc(
                {
                    "doctype": "CC Price Version",
                    "stock_lot": lot.name,
                    "partner_rate": 71,
                    "valid_from": first_start,
                }
            ).insert(ignore_permissions=True)

        second = frappe.get_doc(
            {
                "doctype": "CC Price Version",
                "stock_lot": lot.name,
                "partner_rate": 75,
                "valid_from": second_start,
                "notes": "Approved forward-only revision",
            }
        ).insert(ignore_permissions=True)
        second.submit()
        first.reload()
        self.assertEqual(first.status, "SUPERSEDED")
        self.assertEqual(get_datetime(first.valid_to), get_datetime(second_start))
        self.assertEqual(second.status, "ACTIVE")
        self.assertEqual(second.supersedes, first.name)
        self.assertEqual(
            get_effective_price_version(lot.name, first_start).name,
            first.name,
        )
        self.assertEqual(
            get_effective_price_version(lot.name, second_start).name,
            second.name,
        )

        second.cancel()
        first.reload()
        self.assertEqual(first.status, "ACTIVE")
        self.assertFalse(first.valid_to)
        self.assertEqual(
            get_effective_price_version(lot.name, second_start).name,
            first.name,
        )
