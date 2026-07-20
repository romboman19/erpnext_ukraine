"""Test-site-only two-process probe for the production reservation service."""

from __future__ import annotations

from decimal import Decimal
from time import sleep
from typing import Any

from ..integrations.reservations import reserve_stock
from ..services.allocation import InsufficientStockError
from ..services.reservation import ReservationError, ReservationRequest

ALLOWED_SITE = "postest-restore.local"
CONFIRMATION = "RUN_RESERVATION_PROBE"
PROBE_KEY_PREFIX = "_CC-CONCURRENT-LAST-UNIT"


def _assert_scope(frappe: Any, confirm_site: str, confirm_write: str) -> None:
    if frappe.local.site != ALLOWED_SITE or confirm_site != ALLOWED_SITE:
        raise RuntimeError(f"Reservation probe is restricted to {ALLOWED_SITE}")
    if confirm_write != CONFIRMATION:
        raise RuntimeError(f"Explicit confirmation {CONFIRMATION!r} is required")


def prepare_reservation_probe(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe
    from frappe.utils import nowdate

    from ..integration_tests.test_frappe_foundation import (
        LOCATION,
        _cleanup_integration_records,
    )
    from ..integration_tests.test_frappe_own_receipt import _cleanup_own_receipts
    from ..integration_tests.test_frappe_receipt import (
        _cleanup_receipt_records,
        _ensure_item,
        _ensure_receipt_context,
    )
    from ..integration_tests.test_frappe_reservation import _cleanup_allocations

    _assert_scope(frappe, confirm_site, confirm_write)
    frappe.set_user("Administrator")
    _cleanup_allocations()
    _cleanup_own_receipts()
    _cleanup_receipt_records()
    _cleanup_integration_records()

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
            "posting_time": "13:00:00",
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
                }
            ],
        }
    ).insert(ignore_permissions=True)
    receipt.submit()
    receipt.reload()
    frappe.db.commit()
    return {
        "company": company.name,
        "location": LOCATION,
        "warehouse": warehouses["OWN"],
        "item_code": item_code,
        "receipt": receipt.name,
        "lot": receipt.items[0].stock_lot,
        "available_qty": 1.0,
        "reserved_qty": 0.0,
    }


def attempt_reservation_probe(
    confirm_site: str,
    confirm_write: str,
    contender: str,
) -> dict[str, Any]:
    import frappe

    from ..integration_tests.test_frappe_foundation import COMPANY, LOCATION

    _assert_scope(frappe, confirm_site, confirm_write)
    frappe.set_user("Administrator")
    lot = frappe.db.get_value(
        "CC Stock Lot",
        {"company": COMPANY, "location": LOCATION, "lot_status": "OPEN"},
        ["name", "item_code", "warehouse"],
        as_dict=True,
    )
    if not lot:
        raise RuntimeError("Reservation probe has not been prepared")
    sleep(2)
    allocation_name = None
    error_type = None
    error_message = None
    try:
        allocation = reserve_stock(
            ReservationRequest(
                idempotency_key=f"{PROBE_KEY_PREFIX}:{contender}",
                item_code=lot.item_code,
                company=COMPANY,
                location=LOCATION,
                qty=Decimal("1"),
                allowed_warehouses=frozenset({lot.warehouse}),
            )
        )
        allocation_name = allocation.name
        frappe.db.commit()
    except (InsufficientStockError, ReservationError) as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
        frappe.db.rollback()

    state = frappe.db.get_value(
        "CC Stock Lot",
        lot.name,
        ["received_qty", "reserved_qty"],
        as_dict=True,
    )
    return {
        "contender": contender,
        "success": bool(allocation_name),
        "allocation": allocation_name,
        "error_type": error_type,
        "error_message": error_message,
        "available_qty": float(state.received_qty or 0),
        "reserved_qty": float(state.reserved_qty or 0),
    }


def cleanup_reservation_probe(confirm_site: str, confirm_write: str) -> dict[str, Any]:
    import frappe

    from ..integration_tests.test_frappe_foundation import _cleanup_integration_records
    from ..integration_tests.test_frappe_own_receipt import _cleanup_own_receipts
    from ..integration_tests.test_frappe_receipt import _cleanup_receipt_records
    from ..integration_tests.test_frappe_reservation import _cleanup_allocations

    _assert_scope(frappe, confirm_site, confirm_write)
    frappe.set_user("Administrator")
    _cleanup_allocations()
    _cleanup_own_receipts()
    _cleanup_receipt_records()
    _cleanup_integration_records()
    frappe.db.commit()
    return {
        "active_allocations": frappe.db.count("CC Allocation", {"status": "RESERVED"}),
        "active_probe_lots": frappe.db.count(
            "CC Stock Lot", {"lot_status": "OPEN", "reserved_qty": (">", 0)}
        ),
    }
