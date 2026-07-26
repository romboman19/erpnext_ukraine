"""Clean-site atomic allocation reservation lifecycle."""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime, nowdate

from erpnext_ua.consignment_and_commission.integrations.candidates import (
    CCStockLotCandidateAdapter,
)
from erpnext_ua.consignment_and_commission.integrations.reservations import (
    IdempotencyConflictError,
    consume_allocation,
    expire_due_allocations,
    release_allocation,
    reserve_stock,
)
from erpnext_ua.consignment_and_commission.services.allocation import (
    InsufficientStockError,
)
from erpnext_ua.consignment_and_commission.services.candidates import (
    CandidateQuery,
    preview_from_adapters,
)
from erpnext_ua.consignment_and_commission.services.reservation import (
    ReservationError,
    ReservationRequest,
)

from .test_frappe_foundation import COMPANY, LOCATION, _cleanup_integration_records
from .test_frappe_own_receipt import _cleanup_own_receipts
from .test_frappe_receipt import (
    SERIAL_ITEM,
    _cleanup_receipt_records,
    _enable_item_tracking,
    _ensure_item,
    _ensure_receipt_context,
)


def _cleanup_allocations() -> None:
    from erpnext_ua.consignment_and_commission.doctype.cc_allocation.cc_allocation import (
        TEST_CLEANUP_FLAG,
    )
    from erpnext_ua.consignment_and_commission.services.reservation import (
        ReservationError,
    )

    names = frappe.get_all("CC Allocation", filters={"company": COMPANY}, pluck="name")
    for name in names:
        status = frappe.db.get_value("CC Allocation", name, "status")
        if status == "RESERVED":
            try:
                release_allocation(name, reason="Integration test cleanup")
            except ReservationError:
                pass
    previous = getattr(frappe.flags, TEST_CLEANUP_FLAG, False)
    setattr(frappe.flags, TEST_CLEANUP_FLAG, True)
    try:
        for name in names:
            if frappe.db.exists("CC Allocation", name):
                frappe.delete_doc("CC Allocation", name, force=True, ignore_permissions=True)
    finally:
        setattr(frappe.flags, TEST_CLEANUP_FLAG, previous)
    frappe.db.commit()
    frappe.clear_cache()


class TestFrappeReservation(IntegrationTestCase):
    def test_idempotent_multilot_release_and_expiry(self) -> None:
        _cleanup_allocations()
        _cleanup_own_receipts()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_own_receipts)
        self.addCleanup(_cleanup_allocations)

        company, warehouses, _contract = _ensure_receipt_context()
        settings = frappe.get_single("CC Settings")
        settings.enable_buyout = 1
        settings.reservation_ttl_minutes = 15
        settings.allocation_retry_limit = 3
        settings.save(ignore_permissions=True)
        item_code = _ensure_item()
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
                "posting_time": "11:00:00",
                "supplier": supplier,
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
                        "rate": 40,
                    },
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": 50,
                    },
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()
        lots = [frappe.get_doc("CC Stock Lot", row.stock_lot) for row in receipt.items]

        request = ReservationRequest(
            idempotency_key="_CC-INTEGRATION-RESERVATION-1",
            item_code=item_code,
            company=company.name,
            location=LOCATION,
            qty=Decimal("2"),
            allowed_warehouses=frozenset({warehouses["OWN"]}),
        )
        with self.assertRaisesRegex(ReservationError, "dedicated transaction"):
            reserve_stock(request)
        frappe.db.commit()

        allocation = reserve_stock(request)
        self.assertEqual(allocation.status, "RESERVED")
        self.assertEqual([row.stock_lot for row in allocation.slices], [lot.name for lot in lots])
        self.assertEqual([Decimal(str(row.qty)) for row in allocation.slices], [Decimal("1")] * 2)
        for lot in lots:
            lot.reload()
            self.assertEqual(Decimal(str(lot.reserved_qty)), Decimal("1.0"))

        with self.assertRaisesRegex(ReservationError, "does not exist"):
            consume_allocation(
                allocation.name,
                consumer_doctype="Sales Invoice",
                consumer_document="_CC-MISSING-SALES-INVOICE",
            )

        frappe.db.commit()
        replay = reserve_stock(request)
        self.assertEqual(replay.name, allocation.name)
        for lot in lots:
            lot.reload()
            self.assertEqual(Decimal(str(lot.reserved_qty)), Decimal("1.0"))

        with self.assertRaisesRegex(IdempotencyConflictError, "another request"):
            reserve_stock(
                ReservationRequest(
                    idempotency_key=request.idempotency_key,
                    item_code=item_code,
                    company=company.name,
                    location=LOCATION,
                    qty=Decimal("1"),
                    allowed_warehouses=frozenset({warehouses["OWN"]}),
                )
            )

        with self.assertRaises(InsufficientStockError):
            preview_from_adapters(
                [CCStockLotCandidateAdapter()],
                query=CandidateQuery(
                    item_code=item_code,
                    company=company.name,
                    location=LOCATION,
                    allowed_warehouses=frozenset({warehouses["OWN"]}),
                ),
                qty=Decimal("1"),
            )

        released = release_allocation(allocation.name, reason="Customer abandoned checkout")
        self.assertEqual(released.status, "RELEASED")
        for lot in lots:
            lot.reload()
            self.assertEqual(Decimal(str(lot.reserved_qty)), Decimal("0.0"))

        frappe.db.commit()
        expiring = reserve_stock(
            ReservationRequest(
                idempotency_key="_CC-INTEGRATION-RESERVATION-2",
                item_code=item_code,
                company=company.name,
                location=LOCATION,
                qty=Decimal("1"),
                allowed_warehouses=frozenset({warehouses["OWN"]}),
            )
        )
        frappe.db.set_value(
            "CC Allocation",
            expiring.name,
            "expires_at",
            add_to_date(now_datetime(), seconds=-1, as_datetime=True),
            update_modified=False,
        )
        self.assertEqual(expire_due_allocations(), 1)
        expiring.reload()
        self.assertEqual(expiring.status, "EXPIRED")
        lots[0].reload()
        self.assertEqual(Decimal(str(lots[0].reserved_qty)), Decimal("0.0"))

        with self.assertRaisesRegex(frappe.ValidationError, "server-owned"):
            frappe.get_doc(
                {
                    "doctype": "CC Allocation",
                    "idempotency_key": "MANUAL",
                }
            ).insert(ignore_permissions=True)

    def test_exact_serial_reservation_is_identity_level_and_reusable_after_release(self) -> None:
        _cleanup_allocations()
        _cleanup_own_receipts()
        _cleanup_receipt_records()
        _cleanup_integration_records()
        self.addCleanup(_cleanup_integration_records)
        self.addCleanup(_cleanup_receipt_records)
        self.addCleanup(_cleanup_own_receipts)
        self.addCleanup(_cleanup_allocations)

        company, warehouses, _contract = _ensure_receipt_context()
        settings = frappe.get_single("CC Settings")
        settings.enable_buyout = 1
        settings.save(ignore_permissions=True)
        _enable_item_tracking()
        serial_item = _ensure_item(SERIAL_ITEM, has_serial_no=True)
        serial_numbers = ("_CC-RESERVE-SERIAL-001", "_CC-RESERVE-SERIAL-002")
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
                "posting_time": "12:00:00",
                "supplier": supplier,
                "company": company.name,
                "location": LOCATION,
                "currency": company.default_currency,
                "conversion_rate": 1,
                "items": [
                    {
                        "item_code": serial_item,
                        "qty": 2,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": 30,
                        "serial_numbers": "\n".join(serial_numbers),
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        receipt.reload()
        lot = frappe.get_doc("CC Stock Lot", receipt.items[0].stock_lot)
        frappe.db.commit()

        request = ReservationRequest(
            idempotency_key="_CC-INTEGRATION-SERIAL-1",
            item_code=serial_item,
            company=company.name,
            location=LOCATION,
            qty=Decimal("1"),
            allowed_warehouses=frozenset({warehouses["OWN"]}),
            serial_no=serial_numbers[0],
        )
        allocation = reserve_stock(request)
        self.assertEqual(allocation.slices[0].serial_no, serial_numbers[0])
        lot.reload()
        self.assertEqual(Decimal(str(lot.reserved_qty)), Decimal("1.0"))
        frappe.db.commit()

        with self.assertRaises(InsufficientStockError):
            reserve_stock(
                ReservationRequest(
                    idempotency_key="_CC-INTEGRATION-SERIAL-CONFLICT",
                    item_code=serial_item,
                    company=company.name,
                    location=LOCATION,
                    qty=Decimal("1"),
                    allowed_warehouses=frozenset({warehouses["OWN"]}),
                    serial_no=serial_numbers[0],
                )
            )

        other_preview = preview_from_adapters(
            [CCStockLotCandidateAdapter()],
            query=CandidateQuery(
                item_code=serial_item,
                company=company.name,
                location=LOCATION,
                allowed_warehouses=frozenset({warehouses["OWN"]}),
                serial_no=serial_numbers[1],
            ),
            qty=Decimal("1"),
        )
        self.assertEqual(other_preview[0].serial_no, serial_numbers[1])

        release_allocation(allocation.name, reason="Customer changed selected Serial No")
        frappe.db.commit()
        reused = reserve_stock(
            ReservationRequest(
                idempotency_key="_CC-INTEGRATION-SERIAL-2",
                item_code=serial_item,
                company=company.name,
                location=LOCATION,
                qty=Decimal("1"),
                allowed_warehouses=frozenset({warehouses["OWN"]}),
                serial_no=serial_numbers[0],
            )
        )
        self.assertEqual(reused.slices[0].serial_no, serial_numbers[0])
        release_allocation(reused.name, reason="Integration test complete")
        lot.reload()
        self.assertEqual(Decimal(str(lot.reserved_qty)), Decimal("0.0"))
