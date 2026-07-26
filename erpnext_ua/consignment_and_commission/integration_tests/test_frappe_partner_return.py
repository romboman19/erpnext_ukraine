"""Controlled unsold partner-return lifecycle and reservation safety."""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from erpnext_ua.consignment_and_commission.api.v1 import (
    partner_returns as partner_return_api,
)
from erpnext_ua.consignment_and_commission.integrations.reconciliation import (
    audit_financial_integrity,
)
from erpnext_ua.consignment_and_commission.integrations.reservations import (
    release_allocation,
    reserve_stock,
)
from erpnext_ua.consignment_and_commission.services.partner_return import (
    PartnerReturnError,
)
from erpnext_ua.consignment_and_commission.services.reservation import (
    ReservationRequest,
)
from erpnext_ua.consignment_and_commission.services.stock_lot import (
    get_ownership_balance,
)
from erpnext_ua.consignment_and_commission.setup.ownership_dimension import (
    OWNERSHIP_FIELD,
    PARTNER_RETURN_FIELD,
)

from .test_frappe_foundation import COMPANY, LOCATION, _cleanup_integration_records
from .test_frappe_receipt import (
    SERIAL_ITEM,
    _cleanup_off_balance_records,
    _cleanup_receipt_records,
    _enable_item_tracking,
    _ensure_item,
    _ensure_receipt_context,
)


def _cleanup_partner_returns() -> None:
    if not frappe.db.exists("DocType", "CC Partner Return"):
        return
    rows = frappe.get_all(
        "CC Partner Return",
        filters={"company": COMPANY},
        fields=["name", "stock_entry", "docstatus"],
    )
    for row in rows:
        if row.docstatus == 1 and row.stock_entry:
            frappe.get_doc("CC Partner Return", row.name).cancel()
        elif row.docstatus == 1:
            # Recover audit-incomplete rows left by an interrupted previous test
            # transaction; the production request boundary would roll these back.
            frappe.db.set_value("CC Partner Return", row.name, "docstatus", 0)
    for row in rows:
        if row.stock_entry and frappe.db.exists("Stock Entry", row.stock_entry):
            frappe.delete_doc(
                "Stock Entry",
                row.stock_entry,
                force=True,
                ignore_permissions=True,
            )
        if frappe.db.exists("CC Partner Return", row.name):
            frappe.delete_doc(
                "CC Partner Return",
                row.name,
                force=True,
                ignore_permissions=True,
            )
    frappe.db.commit()


def _cleanup_all() -> None:
    _cleanup_off_balance_records()
    _cleanup_partner_returns()
    _cleanup_receipt_records()
    _cleanup_integration_records()


class TestFrappePartnerReturn(IntegrationTestCase):
    def test_api_create_is_idempotent_and_payload_conflicts_are_rejected(self) -> None:
        _cleanup_all()
        self.addCleanup(_cleanup_all)
        _company, _warehouses, contract = _ensure_receipt_context()
        item_code = _ensure_item()
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": "00:00:30",
                "contract": contract,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "uom": "Nos",
                        "accounting_unit_value": 100,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        lot = receipt.items[0].stock_lot
        idempotency_key = f"partner-return-api-{frappe.generate_hash(length=12)}"
        arguments = {
            "idempotency_key": idempotency_key,
            "posting_date": nowdate(),
            "posting_time": "00:00:31",
            "source_lot": lot,
            "qty": "1.0",
            "reason": "Partner API retry acceptance",
        }
        first = partner_return_api.create(**arguments)
        replay = partner_return_api.create(**{**arguments, "qty": "1"})
        self.assertEqual(first["name"], replay["name"])
        with self.assertRaisesRegex(PartnerReturnError, "belongs to another request"):
            partner_return_api.create(**{**arguments, "qty": "0.5"})

        submitted = partner_return_api.submit(partner_return=first["name"])
        self.assertEqual(submitted["status"], "RETURNED")
        self.assertTrue(submitted["stock_entry"])
        replay_submitted = partner_return_api.submit(partner_return=first["name"])
        self.assertEqual(replay_submitted["stock_entry"], submitted["stock_entry"])
        cancelled = partner_return_api.cancel(partner_return=first["name"])
        self.assertEqual(cancelled["status"], "CANCELLED")

    def test_unreserved_exact_lot_return_posts_zero_value_and_reverses(self) -> None:
        _cleanup_all()
        self.addCleanup(_cleanup_all)
        company, warehouses, contract = _ensure_receipt_context()
        item_code = _ensure_item()
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": "00:01:00",
                "contract": contract,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 2,
                        "uom": "Nos",
                        "accounting_unit_value": 100,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        lot = receipt.items[0].stock_lot
        frappe.db.commit()

        allocation = reserve_stock(
            ReservationRequest(
                idempotency_key=f"_CC-PARTNER-RETURN-HOLD-{frappe.generate_hash(length=10)}",
                item_code=item_code,
                company=company.name,
                location=LOCATION,
                qty=Decimal("2"),
                allowed_warehouses=frozenset({warehouses["COMMISSION"]}),
            )
        )
        frappe.db.commit()
        partner_return = frappe.get_doc(
            {
                "doctype": "CC Partner Return",
                "posting_date": nowdate(),
                "posting_time": "00:02:00",
                "source_lot": lot,
                "qty": 1,
                "reason": "Unsold stock requested back by partner",
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
        with self.assertRaisesRegex(frappe.ValidationError, "unreserved balance 0"):
            partner_return.submit()
        partner_return.reload()
        self.assertEqual(partner_return.docstatus, 0)

        release_allocation(allocation.name, reason="Partner return acceptance test")
        frappe.db.commit()
        partner_return.submit()
        partner_return.reload()
        self.assertEqual(partner_return.status, "RETURNED")
        self.assertEqual(Decimal(str(partner_return.off_balance_amount)), Decimal("100.0"))
        account_024 = frappe.get_doc(
            "UA Off Balance Entry",
            partner_return.off_balance_entry,
        )
        self.assertEqual(account_024.docstatus, 1)
        self.assertEqual(account_024.direction, "Decrease")
        self.assertEqual(Decimal(str(account_024.quantity)), Decimal("1.0"))
        self.assertEqual(Decimal(str(account_024.amount)), Decimal("100.0"))
        self.assertEqual(account_024.reference_doctype, "CC Partner Return")
        self.assertEqual(account_024.reference_name, partner_return.name)
        self.assertEqual(get_ownership_balance(lot), Decimal("1.0"))
        stock_entry = frappe.get_doc("Stock Entry", partner_return.stock_entry)
        self.assertEqual(stock_entry.docstatus, 1)
        self.assertEqual(stock_entry.get(PARTNER_RETURN_FIELD), partner_return.name)
        self.assertEqual(stock_entry.items[0].get(OWNERSHIP_FIELD), lot)
        self.assertFalse(
            frappe.db.exists(
                "GL Entry",
                {
                    "voucher_type": "Stock Entry",
                    "voucher_no": stock_entry.name,
                    "is_cancelled": 0,
                },
            )
        )
        with self.assertRaisesRegex(frappe.ValidationError, "Cancel linked CC Partner Return"):
            stock_entry.cancel()
        with self.assertRaisesRegex(frappe.ValidationError, "Cancel submitted CC Partner Returns"):
            receipt.cancel()
        audit = audit_financial_integrity(company=company.name)
        self.assertTrue(audit["ok"], audit["issues"])
        self.assertEqual(audit["checked"]["partner_returns"], 1)

        partner_return.cancel()
        account_024.reload()
        self.assertEqual(account_024.docstatus, 2)
        self.assertEqual(get_ownership_balance(lot), Decimal("2.0"))
        self.assertEqual(frappe.db.get_value("CC Stock Lot", lot, "lot_status"), "OPEN")

    def test_serial_partner_return_requires_and_moves_exact_identity(self) -> None:
        _cleanup_all()
        self.addCleanup(_cleanup_all)
        _enable_item_tracking()
        _company, _warehouses, contract = _ensure_receipt_context()
        item_code = _ensure_item(SERIAL_ITEM, has_serial_no=True)
        serials = ("_CC-PARTNER-RETURN-SERIAL-1", "_CC-PARTNER-RETURN-SERIAL-2")
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": "00:03:00",
                "contract": contract,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 2,
                        "uom": "Nos",
                        "accounting_unit_value": 100,
                        "serial_numbers": "\n".join(serials),
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        lot = receipt.items[0].stock_lot
        partner_return = frappe.get_doc(
            {
                "doctype": "CC Partner Return",
                "posting_date": nowdate(),
                "posting_time": "00:04:00",
                "source_lot": lot,
                "qty": 1,
                "serial_numbers": serials[0],
                "reason": "Return one exact serialized unit",
            }
        ).insert(ignore_permissions=True)
        partner_return.submit()
        self.assertEqual(get_ownership_balance(lot), Decimal("1.0"))
        self.assertFalse(frappe.db.get_value("Serial No", serials[0], "warehouse"))
        self.assertEqual(
            frappe.db.get_value("Serial No", serials[1], "warehouse"),
            receipt.warehouse,
        )
        self.assertEqual(
            frappe.db.get_value("Serial No", serials[0], OWNERSHIP_FIELD),
            lot,
        )

        partner_return.cancel()
        self.assertEqual(get_ownership_balance(lot), Decimal("2.0"))
        self.assertEqual(
            {frappe.db.get_value("Serial No", serial_no, "warehouse") for serial_no in serials},
            {receipt.warehouse},
        )
