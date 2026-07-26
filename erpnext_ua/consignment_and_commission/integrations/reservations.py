"""Atomic, idempotent reservation of global FIFO allocation slices."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from contextlib import contextmanager
from decimal import Decimal
from hashlib import sha256
from typing import Any

from ..doctype.cc_allocation.cc_allocation import WRITE_FLAG
from ..services.allocation import AllocationSlice
from ..services.candidates import CandidateAdapter, CandidateQuery, preview_from_adapters
from ..services.reservation import (
    ReservationError,
    ReservationRequest,
    reservation_fingerprint,
    validate_allocation_transition,
    validate_reservation_request,
)
from ..services.stock_lot import get_ownership_balance


class ReservationConflictError(ReservationError):
    """Raised when another transaction acquires one of the requested slices."""


class IdempotencyConflictError(ReservationError):
    """Raised when an idempotency key is reused for a different payload."""


class ReservationExpiredError(ReservationError):
    """Raised when a consumer tries to use a reservation past its TTL."""


@contextmanager
def _allocation_write(frappe: Any):
    previous = getattr(frappe.flags, WRITE_FLAG, False)
    setattr(frappe.flags, WRITE_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, WRITE_FLAG, previous)


def _validate_scope(frappe: Any, request: ReservationRequest) -> None:
    settings = frappe.get_single("CC Settings")
    if not settings.enabled:
        raise ReservationError("CC Settings must be enabled before stock can be reserved")
    location = frappe.db.get_value(
        "CC Location",
        request.location,
        [
            "company",
            "disabled",
            "own_warehouse",
            "commission_warehouse",
            "consignment_warehouse",
        ],
        as_dict=True,
    )
    if not location or location.disabled or location.company != request.company:
        raise ReservationError("Reservation requires an enabled Location for its Company")
    technical_warehouses = {
        location.own_warehouse,
        location.commission_warehouse,
        location.consignment_warehouse,
    }
    if not request.allowed_warehouses.issubset(technical_warehouses):
        raise ReservationError("Reservation Warehouses must belong to the selected CC Location")


def _existing_allocation(
    frappe: Any,
    request: ReservationRequest,
    fingerprint: str,
    *,
    current_read: bool = False,
) -> Any | None:
    if current_read:
        rows = frappe.db.sql(
            """
            select name
            from `tabCC Allocation`
            where idempotency_key = %s
            for update
            """,
            (request.idempotency_key,),
        )
        name = rows[0][0] if rows else None
    else:
        name = frappe.db.get_value(
            "CC Allocation",
            {"idempotency_key": request.idempotency_key},
            "name",
        )
    if not name:
        return None
    allocation = frappe.get_doc("CC Allocation", name)
    if allocation.request_fingerprint != fingerprint:
        raise IdempotencyConflictError(
            f"Idempotency key {request.idempotency_key!r} already belongs to another request"
        )
    if allocation.status == "PENDING":
        raise ReservationConflictError(f"CC Allocation {allocation.name} is still pending")
    if allocation.status == "RESERVED":
        from frappe.utils import get_datetime, now_datetime

        if get_datetime(allocation.expires_at) <= now_datetime():
            return _finish_allocation(
                allocation.name,
                target_status="EXPIRED",
                reason="Reservation TTL expired during idempotent lookup",
            )
    return allocation


def _new_allocation(
    frappe: Any,
    *,
    request: ReservationRequest,
    fingerprint: str,
    slices: list[AllocationSlice],
    reserved_at: Any,
    expires_at: Any,
) -> Any:
    allocation = frappe.get_doc(
        {
            "doctype": "CC Allocation",
            "status": "PENDING",
            "idempotency_key": request.idempotency_key,
            "request_fingerprint": fingerprint,
            "company": request.company,
            "location": request.location,
            "item_code": request.item_code,
            "requested_qty": float(request.qty),
            "serial_no": request.serial_no,
            "batch_no": request.batch_no,
            "fiscal_policy": request.fiscal_policy,
            "allowed_warehouses": "\n".join(sorted(request.allowed_warehouses)),
            "reserved_at": reserved_at,
            "expires_at": expires_at,
            "total_reserved_qty": float(request.qty),
        }
    )
    for row in slices:
        allocation.append(
            "slices",
            {
                "sequence": row.sequence,
                "stock_lot": row.lot_name,
                "warehouse": row.warehouse,
                "source_method": row.source_method,
                "relationship_model": row.relationship_model,
                "qty": float(row.qty),
                "serial_no": row.serial_no,
                "batch_no": row.batch_no,
                "fifo_datetime": row.fifo_datetime,
                "receipt_name": row.receipt_name,
                "receipt_row_index": row.receipt_row_index,
            },
        )
    allocation_name = "CC-ALLOC-" + sha256(
        f"{request.idempotency_key}:{fingerprint}".encode()
    ).hexdigest()[:20].upper()
    with _allocation_write(frappe):
        allocation.insert(ignore_permissions=True, set_name=allocation_name)
    return allocation


def _lock_lot(frappe: Any, lot_name: str) -> Any:
    rows = frappe.db.sql(
        """
        select name, lot_status, company, location, item_code, warehouse,
               source_method, relationship_model, reserved_qty
        from `tabCC Stock Lot`
        where name = %s
        for update
        """,
        (lot_name,),
        as_dict=True,
    )
    if not rows:
        raise ReservationConflictError(f"CC Stock Lot {lot_name} no longer exists")
    return rows[0]


def _assert_serials_unreserved(frappe: Any, lot_name: str, serial_numbers: set[str]) -> None:
    if not serial_numbers:
        return
    placeholders = ", ".join(["%s"] * len(serial_numbers))
    rows = frappe.db.sql(
        f"""
        select slice.serial_no
        from `tabCC Allocation Slice` slice
        inner join `tabCC Allocation` allocation on allocation.name = slice.parent
        where slice.stock_lot = %s
          and slice.serial_no in ({placeholders})
          and allocation.status = 'RESERVED'
        for update
        """,
        (lot_name, *sorted(serial_numbers)),
        as_dict=True,
    )
    if rows:
        raise ReservationConflictError(
            f"Serial No {rows[0].serial_no} is already reserved in CC Stock Lot {lot_name}"
        )


def _reserve_slices(
    frappe: Any,
    *,
    request: ReservationRequest,
    slices: list[AllocationSlice],
) -> None:
    quantities: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    serials: dict[str, set[str]] = defaultdict(set)
    slices_by_lot: dict[str, list[AllocationSlice]] = defaultdict(list)
    for row in slices:
        quantities[row.lot_name] += row.qty
        slices_by_lot[row.lot_name].append(row)
        if row.serial_no:
            if row.serial_no in serials[row.lot_name]:
                raise ReservationConflictError(f"Serial No {row.serial_no} appears twice in allocation")
            serials[row.lot_name].add(row.serial_no)

    for lot_name in sorted(quantities):
        lot = _lock_lot(frappe, lot_name)
        expected_slices = slices_by_lot[lot_name]
        first = expected_slices[0]
        expected = {
            "lot_status": "OPEN",
            "company": request.company,
            "location": request.location,
            "item_code": request.item_code,
            "warehouse": first.warehouse,
            "source_method": first.source_method,
            "relationship_model": first.relationship_model,
        }
        mismatches = [
            fieldname for fieldname, value in expected.items() if str(lot.get(fieldname)) != str(value)
        ]
        if mismatches:
            raise ReservationConflictError(
                f"CC Stock Lot {lot_name} changed during allocation: {', '.join(mismatches)}"
            )
        _assert_serials_unreserved(frappe, lot_name, serials[lot_name])
        active_balance = get_ownership_balance(lot_name)
        reserve_qty = quantities[lot_name]
        frappe.db.sql(
            """
            update `tabCC Stock Lot`
            set reserved_qty = reserved_qty + %s
            where name = %s
              and lot_status = 'OPEN'
              and reserved_qty + %s <= %s
            """,
            (float(reserve_qty), lot_name, float(reserve_qty), float(active_balance)),
        )
        affected = int(frappe.db.sql("select row_count()")[0][0])
        if affected != 1:
            raise ReservationConflictError(
                f"CC Stock Lot {lot_name} no longer has {reserve_qty} allocatable quantity"
            )


def reserve_stock(
    request: ReservationRequest,
    *,
    adapters: Sequence[CandidateAdapter] | None = None,
) -> Any:
    """Reserve one logical request as all-or-nothing global FIFO slices.

    This is a dedicated hold-transaction boundary and must run before unrelated
    writes. MariaDB can roll back the full transaction when two identical
    idempotent inserts race; the service then safely retries only its own work.
    """
    import frappe
    from frappe.utils import add_to_date, now_datetime

    if frappe.db.transaction_writes:
        raise ReservationError(
            "Stock reservation must start in a dedicated transaction before unrelated writes"
        )
    validate_reservation_request(request)
    _validate_scope(frappe, request)
    fingerprint = reservation_fingerprint(request)
    existing = _existing_allocation(frappe, request, fingerprint)
    if existing:
        return existing

    settings = frappe.get_single("CC Settings")
    retry_limit = int(settings.allocation_retry_limit or 0)
    ttl_minutes = int(settings.reservation_ttl_minutes or 0)
    source_adapters = list(adapters) if adapters is not None else None

    for attempt in range(1, retry_limit + 1):
        savepoint = f"cc_reservation_{attempt}"
        frappe.db.savepoint(savepoint)
        query = CandidateQuery(
            item_code=request.item_code,
            company=request.company,
            location=request.location,
            allowed_warehouses=request.allowed_warehouses,
            serial_no=request.serial_no,
            batch_no=request.batch_no,
            fiscal_policy=request.fiscal_policy,
        )
        if source_adapters is None:
            from .candidates import CCStockLotCandidateAdapter

            source_adapters = [CCStockLotCandidateAdapter()]
        slices = preview_from_adapters(source_adapters, query=query, qty=request.qty)
        reserved_at = now_datetime()
        expires_at = add_to_date(reserved_at, minutes=ttl_minutes, as_datetime=True)
        try:
            allocation = _new_allocation(
                frappe,
                request=request,
                fingerprint=fingerprint,
                slices=slices,
                reserved_at=reserved_at,
                expires_at=expires_at,
            )
            _reserve_slices(frappe, request=request, slices=slices)
            allocation.status = "RESERVED"
            with _allocation_write(frappe):
                allocation.save(ignore_permissions=True)
            return allocation
        except frappe.DuplicateEntryError:
            frappe.db.rollback(save_point=savepoint)
            existing = _existing_allocation(frappe, request, fingerprint)
            if existing:
                return existing
            if attempt == retry_limit:
                raise ReservationConflictError("Concurrent idempotent allocation did not settle")
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            existing = _existing_allocation(
                frappe,
                request,
                fingerprint,
                current_read=True,
            )
            if existing:
                return existing
            if attempt == retry_limit:
                raise ReservationConflictError("Concurrent allocation database conflict did not settle")
        except ReservationConflictError:
            frappe.db.rollback(save_point=savepoint)
            if attempt == retry_limit:
                raise
    raise ReservationConflictError("Reservation retry limit was exhausted")


def _finish_allocation(
    allocation_name: str,
    *,
    target_status: str,
    reason: str | None = None,
    consumer_doctype: str | None = None,
    consumer_document: str | None = None,
) -> Any:
    import frappe
    from frappe.utils import now_datetime

    rows = frappe.db.sql(
        "select status, expires_at from `tabCC Allocation` where name = %s for update",
        (allocation_name,),
        as_dict=True,
    )
    if not rows:
        raise ReservationError(f"CC Allocation {allocation_name} does not exist")
    current_status = rows[0].status
    if current_status == target_status:
        return frappe.get_doc("CC Allocation", allocation_name)
    if current_status in {"CONSUMED", "RELEASED", "EXPIRED"}:
        raise ReservationError(
            f"CC Allocation {allocation_name} is already terminal in {current_status}"
        )
    validate_allocation_transition(current_status, target_status)
    if target_status == "CONSUMED":
        from frappe.utils import get_datetime

        if get_datetime(rows[0].expires_at) <= now_datetime():
            raise ReservationExpiredError(f"CC Allocation {allocation_name} has expired")
    allocation = frappe.get_doc("CC Allocation", allocation_name)

    quantities: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in allocation.slices:
        quantities[row.stock_lot] += Decimal(str(row.qty))
    for lot_name in sorted(quantities):
        _lock_lot(frappe, lot_name)
        qty = quantities[lot_name]
        frappe.db.sql(
            """
            update `tabCC Stock Lot`
            set reserved_qty = reserved_qty - %s
            where name = %s and reserved_qty >= %s
            """,
            (float(qty), lot_name, float(qty)),
        )
        affected = int(frappe.db.sql("select row_count()")[0][0])
        if affected != 1:
            raise ReservationError(
                f"CC Stock Lot {lot_name} reservation aggregate cannot release {qty}"
            )

    allocation.status = target_status
    allocation.completed_at = now_datetime()
    allocation.release_reason = reason
    allocation.consumer_doctype = consumer_doctype
    allocation.consumer_document = consumer_document
    with _allocation_write(frappe):
        allocation.save(ignore_permissions=True)
    return allocation


def release_allocation(allocation_name: str, *, reason: str) -> Any:
    if not reason or not reason.strip():
        raise ReservationError("Reservation release requires a reason")
    return _finish_allocation(allocation_name, target_status="RELEASED", reason=reason.strip())


def consume_allocation(
    allocation_name: str,
    *,
    consumer_doctype: str,
    consumer_document: str,
) -> Any:
    import frappe

    if not consumer_doctype or not consumer_document:
        raise ReservationError("Consumed reservation requires its consumer document")
    if not frappe.db.exists("DocType", consumer_doctype):
        raise ReservationError(f"Consumer DocType {consumer_doctype} does not exist")
    if not frappe.db.exists(consumer_doctype, consumer_document):
        raise ReservationError(
            f"Consumer document {consumer_doctype} {consumer_document} does not exist"
        )
    return _finish_allocation(
        allocation_name,
        target_status="CONSUMED",
        consumer_doctype=consumer_doctype,
        consumer_document=consumer_document,
    )


def expire_due_allocations(limit: int = 500) -> int:
    import frappe
    from frappe.utils import now_datetime

    names = frappe.get_all(
        "CC Allocation",
        filters={"status": "RESERVED", "expires_at": ("<=", now_datetime())},
        order_by="expires_at asc, name asc",
        limit=max(1, min(int(limit), 5_000)),
        pluck="name",
    )
    expired = 0
    for name in names:
        try:
            _finish_allocation(name, target_status="EXPIRED", reason="Reservation TTL expired")
            expired += 1
        except ReservationError:
            frappe.log_error(
                title=f"CC Allocation expiry failed: {name}",
                message=frappe.get_traceback(),
            )
    return expired
