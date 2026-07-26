"""Clean-site OWN receipt, payable and candidate integration."""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from erpnext_ua.consignment_and_commission.integrations.candidates import (
    CCStockLotCandidateAdapter,
)
from erpnext_ua.consignment_and_commission.services.candidates import (
    CandidateAdapterError,
    CandidateQuery,
    preview_from_adapters,
)
from erpnext_ua.consignment_and_commission.services.stock_lot import (
    get_ownership_balance,
)
from erpnext_ua.consignment_and_commission.setup.ownership_dimension import (
    OWN_RECEIPT_FIELD,
    OWNERSHIP_FIELD,
)

from .test_frappe_foundation import COMPANY, LOCATION, _cleanup_integration_records
from .test_frappe_receipt import (
    BATCH_ITEM,
    SERIAL_ITEM,
    _cleanup_receipt_records,
    _enable_item_tracking,
    _ensure_item,
    _ensure_receipt_context,
)


def _cleanup_own_receipts() -> None:
    receipt_names = frappe.get_all("CC Own Receipt", filters={"company": COMPANY}, pluck="name")
    invoice_names = set(
        frappe.get_all(
            "Purchase Invoice",
            filters={OWN_RECEIPT_FIELD: ("in", receipt_names)},
            pluck="name",
        )
        if receipt_names
        else []
    )
    for receipt_name in receipt_names:
        receipt = frappe.get_doc("CC Own Receipt", receipt_name)
        if receipt.purchase_invoice:
            invoice_names.add(receipt.purchase_invoice)
        if receipt.docstatus == 1 and receipt.purchase_invoice:
            receipt.cancel()
        elif receipt.docstatus == 1:
            frappe.db.set_value("CC Own Receipt", receipt.name, "docstatus", 2, update_modified=False)

    lot_names = frappe.get_all(
        "CC Stock Lot",
        filters={"own_receipt": ("in", receipt_names)},
        pluck="name",
    ) if receipt_names else []
    if lot_names:
        for doctype in ("Batch", "Serial No"):
            for tracking_name in frappe.get_all(
                doctype,
                filters={OWNERSHIP_FIELD: ("in", lot_names)},
                pluck="name",
            ):
                frappe.db.set_value(
                    doctype,
                    tracking_name,
                    OWNERSHIP_FIELD,
                    None,
                    update_modified=False,
                )

    for receipt_name in receipt_names:
        for row_name in frappe.get_all(
            "CC Own Receipt Item", filters={"parent": receipt_name}, pluck="name"
        ):
            frappe.db.set_value(
                "CC Own Receipt Item",
                row_name,
                {"stock_lot": None, "purchase_invoice_item": None},
                update_modified=False,
            )
        frappe.db.set_value(
            "CC Own Receipt", receipt_name, "purchase_invoice", None, update_modified=False
        )
    for lot_name in lot_names:
        frappe.db.set_value(
            "CC Stock Lot",
            lot_name,
            {"purchase_invoice": None, "purchase_invoice_item": None},
            update_modified=False,
        )
    for invoice_name in invoice_names:
        if frappe.db.exists("Purchase Invoice", invoice_name):
            invoice = frappe.get_doc("Purchase Invoice", invoice_name)
            if invoice.docstatus == 1:
                invoice.flags.ignore_links = True
                invoice.cancel()
            frappe.delete_doc("Purchase Invoice", invoice_name, force=True, ignore_permissions=True)
    for lot_name in lot_names:
        if frappe.db.exists("CC Stock Lot", lot_name):
            frappe.delete_doc("CC Stock Lot", lot_name, force=True, ignore_permissions=True)
    for receipt_name in receipt_names:
        if frappe.db.exists("CC Own Receipt", receipt_name):
            frappe.delete_doc("CC Own Receipt", receipt_name, force=True, ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache()


class TestFrappeOwnReceipt(IntegrationTestCase):
    def test_buyout_deferred_debt_and_global_fifo(self) -> None:
        _cleanup_own_receipts()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_own_receipts)

        company, warehouses, _contract = _ensure_receipt_context()
        settings = frappe.get_single("CC Settings")
        settings.enable_buyout = 1
        settings.enable_deferred_purchase = 1
        settings.save(ignore_permissions=True)
        item_code = _ensure_item()
        supplier = frappe.db.get_value(
            "CC Partner Profile",
            {"partner_name": "_CC Integration Partner"},
            "supplier",
        )

        buyout = frappe.get_doc(
            {
                "doctype": "CC Own Receipt",
                "source_method": "BUYOUT",
                "posting_date": nowdate(),
                "posting_time": "08:00:00",
                "supplier": supplier,
                "company": company.name,
                "location": LOCATION,
                "currency": company.default_currency,
                "conversion_rate": 1,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": 40,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        buyout.submit()
        buyout.reload()

        deferred = frappe.get_doc(
            {
                "doctype": "CC Own Receipt",
                "source_method": "DEFERRED_PURCHASE",
                "posting_date": nowdate(),
                "posting_time": "09:00:00",
                "due_date": add_days(nowdate(), 30),
                "supplier": buyout.supplier,
                "company": company.name,
                "location": LOCATION,
                "currency": company.default_currency,
                "conversion_rate": 1,
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": 50,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        deferred.submit()
        deferred.reload()

        buyout_invoice = frappe.get_doc("Purchase Invoice", buyout.purchase_invoice)
        deferred_invoice = frappe.get_doc("Purchase Invoice", deferred.purchase_invoice)
        self.assertEqual(buyout_invoice.due_date, getdate(nowdate()))
        self.assertEqual(deferred_invoice.due_date, getdate(add_days(nowdate(), 30)))
        self.assertEqual(Decimal(str(buyout_invoice.outstanding_amount)), Decimal("80.0"))
        self.assertEqual(Decimal(str(deferred_invoice.outstanding_amount)), Decimal("50.0"))

        buyout_lot = frappe.get_doc("CC Stock Lot", buyout.items[0].stock_lot)
        deferred_lot = frappe.get_doc("CC Stock Lot", deferred.items[0].stock_lot)
        self.assertEqual(buyout_lot.source_method, "BUYOUT")
        self.assertEqual(deferred_lot.source_method, "DEFERRED_PURCHASE")
        self.assertEqual(buyout_lot.relationship_model, "OWN")
        self.assertEqual(get_ownership_balance(buyout_lot.name), 2)
        self.assertEqual(get_ownership_balance(deferred_lot.name), 1)

        query = CandidateQuery(
            item_code=item_code,
            company=company.name,
            location=LOCATION,
            allowed_warehouses=frozenset({warehouses["OWN"]}),
        )
        preview = preview_from_adapters(
            [CCStockLotCandidateAdapter()],
            query=query,
            qty=Decimal("3"),
        )
        self.assertEqual([row.lot_name for row in preview], [buyout_lot.name, deferred_lot.name])
        self.assertEqual([row.source_method for row in preview], ["BUYOUT", "DEFERRED_PURCHASE"])

        from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

        native_receipt = make_stock_entry(
            item_code=item_code,
            company=company.name,
            to_warehouse=warehouses["OWN"],
            qty=1,
            rate=1,
        )
        with self.assertRaisesRegex(CandidateAdapterError, "unclassified stock"):
            CCStockLotCandidateAdapter().load(query)
        native_receipt.cancel()

        buyout_invoice.reload()
        with self.assertRaisesRegex(frappe.ValidationError, "Cancel linked CC Own Receipt"):
            buyout_invoice.cancel()
        buyout_invoice.reload()
        self.assertEqual(buyout_invoice.docstatus, 1)

        deferred.cancel()
        buyout.cancel()
        for lot in (buyout_lot, deferred_lot):
            lot.reload()
            self.assertEqual(lot.lot_status, "CANCELLED")
            self.assertEqual(get_ownership_balance(lot.name), 0)

    def test_owned_batch_and_serial_identity_follow_purchase_invoice(self) -> None:
        _cleanup_own_receipts()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_own_receipts)

        company, warehouses, _contract = _ensure_receipt_context()
        settings = frappe.get_single("CC Settings")
        settings.enable_buyout = 1
        settings.save(ignore_permissions=True)
        _enable_item_tracking()
        batch_item = _ensure_item(BATCH_ITEM, has_batch_no=True)
        serial_item = _ensure_item(SERIAL_ITEM, has_serial_no=True)
        serial_numbers = ("_CC-OWN-SERIAL-001", "_CC-OWN-SERIAL-002")
        supplier = frappe.db.get_value(
            "CC Partner Profile",
            {"partner_name": "_CC Integration Partner"},
            "supplier",
        )

        receipt = frappe.get_doc(
            {
                "doctype": "CC Own Receipt",
                "source_method": "BUYOUT",
                "posting_date": nowdate(),
                "posting_time": "10:00:00",
                "supplier": supplier,
                "company": company.name,
                "location": LOCATION,
                "currency": company.default_currency,
                "conversion_rate": 1,
                "items": [
                    {
                        "item_code": batch_item,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": 25,
                    },
                    {
                        "item_code": serial_item,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": 30,
                        "serial_numbers": "\n".join(serial_numbers),
                    },
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()

        batch_line, serial_line = receipt.items
        self.assertTrue(batch_line.batch_no)
        self.assertEqual(tuple(serial_line.serial_numbers.splitlines()), serial_numbers)
        tracked = [
            ("Batch", batch_line.batch_no, batch_line.stock_lot),
            *(("Serial No", serial_no, serial_line.stock_lot) for serial_no in serial_numbers),
        ]
        for doctype, identity, lot_name in tracked:
            self.assertEqual(frappe.db.get_value(doctype, identity, OWNERSHIP_FIELD), lot_name)

        batch_preview = preview_from_adapters(
            [CCStockLotCandidateAdapter()],
            query=CandidateQuery(
                item_code=batch_item,
                company=company.name,
                location=LOCATION,
                allowed_warehouses=frozenset({warehouses["OWN"]}),
                batch_no=batch_line.batch_no,
            ),
            qty=Decimal("1"),
        )
        self.assertEqual(batch_preview[0].lot_name, batch_line.stock_lot)
        serial_preview = preview_from_adapters(
            [CCStockLotCandidateAdapter()],
            query=CandidateQuery(
                item_code=serial_item,
                company=company.name,
                location=LOCATION,
                allowed_warehouses=frozenset({warehouses["OWN"]}),
                serial_no=serial_numbers[0],
            ),
            qty=Decimal("1"),
        )
        self.assertEqual(serial_preview[0].lot_name, serial_line.stock_lot)
        self.assertEqual(serial_preview[0].serial_no, serial_numbers[0])

        receipt.cancel()
        for line in receipt.items:
            self.assertEqual(get_ownership_balance(line.stock_lot), 0)
