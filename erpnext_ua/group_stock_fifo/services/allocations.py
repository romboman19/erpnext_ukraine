"""The reservation write path (§12.4, §13.1–§13.4).

This is the transaction that decides who sells whose stock, so it is also the
one place where two cashiers can sell the same unit. Three things keep that
from happening, and all three have to hold at once:

* the §13.2 lock order, taken identically by every service, so two requests
  cannot deadlock by grabbing the same rows in opposite directions;
* a conditional `UPDATE` on each position guarded by the **ledger** balance
  read at that moment, so a reservation that no longer fits simply affects zero
  rows instead of quietly overselling;
* the retry loop, because losing that race is normal and should cost a retry,
  not an error to the cashier.
"""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from .candidates import source_warehouses
from .domain import GSFError, balance_identity
from .reservation import (
    ALLOCATION_CONSUMED,
    ALLOCATION_EXPIRED,
    ALLOCATION_PENDING,
    ALLOCATION_RELEASED,
    ALLOCATION_RESERVED,
    LIVE_ALLOCATION_STATUSES,
    ReservationRequest,
    allocation_identity,
    allocation_retry_delay,
    needs_compensation,
    reservation_fingerprint,
    scope_lock_identity,
    validate_allocation_transition,
    validate_reservation_request,
)

WRITE_FLAG = "gsf_allocation_service"


@contextmanager
def _service_write():
    """The allocation controller refuses writes that did not come from here."""
    setattr(frappe.flags, WRITE_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, WRITE_FLAG, False)


def reserve(request: ReservationRequest) -> Any:
    """Reserve exact global FIFO slices, all or nothing (§12.4).

    §13.1 requires this to be the first write of its transaction: the locks
    below are held until commit, and piggybacking on an already-dirty
    transaction would extend that hold across unrelated work.
    """
    if frappe.db.transaction_writes:
        raise GSFError(
            "Reservation must start in a dedicated transaction before unrelated writes",
            "ALLOCATION_CONFLICT",
        )
    validate_reservation_request(request)
    _assert_scope(request)
    fingerprint = reservation_fingerprint(request)

    existing = _existing_allocation(request, fingerprint)
    if existing:
        return existing

    settings = frappe.get_single("GSF Settings")
    retry_limit = max(int(settings.allocation_retry_limit or 1), 1)
    ttl_minutes = int(settings.allocation_ttl_minutes or 0)

    # The validation/idempotency preflight above uses consistent reads. Reset
    # that read-only transaction so the scope lock becomes the first statement
    # of the write transaction and every later candidate/balance read sees the
    # state committed by the previous lock owner. Without this reset, MariaDB
    # can correctly reject an UPDATE against that older snapshot as error 1020.
    frappe.db.rollback()

    for attempt in range(1, retry_limit + 1):
        savepoint = f"gsf_reservation_{attempt}"
        frappe.db.savepoint(savepoint)
        try:
            _lock_scope(request)
            slices = _plan(request)
            reserved_at = now_datetime()
            allocation = _new_allocation(
                request,
                fingerprint=fingerprint,
                slices=slices,
                reserved_at=reserved_at,
                expires_at=add_to_date(reserved_at, minutes=ttl_minutes, as_datetime=True),
            )
            _hold_positions(request, slices)
            allocation.status = ALLOCATION_RESERVED
            with _service_write():
                allocation.save(ignore_permissions=True)
            return allocation
        except frappe.DuplicateEntryError:
            # Two identical requests raced. Whoever lost reads the winner's row.
            frappe.db.rollback(save_point=savepoint)
            settled = _existing_allocation(request, fingerprint)
            if settled:
                return settled
            if attempt == retry_limit:
                raise GSFError(
                    "Concurrent identical allocation did not settle", "ALLOCATION_CONFLICT"
                ) from None
            _wait_before_retry(request, attempt)
        except frappe.QueryDeadlockError:
            # A deadlock rolls back the whole transaction, not just a savepoint.
            frappe.db.rollback()
            if attempt == retry_limit:
                raise GSFError(
                    "Concurrent allocation deadlock did not settle", "ALLOCATION_CONFLICT"
                ) from None
            _wait_before_retry(request, attempt)
        except GSFError as error:
            frappe.db.rollback(save_point=savepoint)
            if error.code != "ALLOCATION_CONFLICT" or attempt == retry_limit:
                raise
            _wait_before_retry(request, attempt)
    raise GSFError("Reservation retry limit was exhausted", "ALLOCATION_CONFLICT")


def _wait_before_retry(request: ReservationRequest, attempt: int) -> None:
    time.sleep(allocation_retry_delay(request.idempotency_key, attempt))


def lock_scope(request: ReservationRequest) -> str:
    """Acquire the shared physical FIFO lock for a composite reservation."""
    validate_reservation_request(request)
    _assert_scope(request)
    return _lock_scope(request)


def reserve_planned(
    request: ReservationRequest,
    slices: list,
    *,
    scope_locked: bool = False,
) -> Any:
    """Reserve exact GSF slices selected by the shared domain planner.

    Composite fulfillment plans all providers while holding the same scope
    lock.  This method keeps GSF as the only writer of its layer balances.
    """
    validate_reservation_request(request)
    _assert_scope(request)
    planned = list(slices)
    _validate_planned_slices(request, planned)
    fingerprint = reservation_fingerprint(request)
    existing = _existing_allocation(request, fingerprint)
    if existing:
        return existing
    if not scope_locked:
        _lock_scope(request)
    settings = frappe.get_single("GSF Settings")
    reserved_at = now_datetime()
    allocation = _new_allocation(
        request,
        fingerprint=fingerprint,
        slices=planned,
        reserved_at=reserved_at,
        expires_at=add_to_date(
            reserved_at,
            minutes=int(settings.allocation_ttl_minutes or 0),
            as_datetime=True,
        ),
    )
    _hold_positions(request, planned)
    allocation.status = ALLOCATION_RESERVED
    with _service_write():
        allocation.save(ignore_permissions=True)
    return allocation


def _validate_planned_slices(request: ReservationRequest, slices: list) -> None:
    if not slices:
        raise GSFError("Shared FIFO returned no GSF slices", "ALLOCATION_CONFLICT")
    total = sum((Decimal(str(row.qty)) for row in slices), Decimal("0"))
    if total != Decimal(str(request.qty)):
        raise GSFError(
            f"Shared FIFO planned {total} instead of requested GSF quantity {request.qty}",
            "ALLOCATION_CONFLICT",
        )
    invalid = [
        row.lot_name
        for row in slices
        if row.warehouse not in request.allowed_warehouses
        or row.source_method != "GSF_LAYER"
        or Decimal(str(row.qty)) <= 0
        or (request.serial_no and row.serial_no != request.serial_no)
        or (request.batch_no and row.batch_no != request.batch_no)
    ]
    if invalid:
        raise GSFError(
            "Shared FIFO produced invalid GSF slices: " + ", ".join(sorted(set(invalid))),
            "ALLOCATION_CONFLICT",
        )


def _assert_scope(request: ReservationRequest) -> None:
    """§12.6: the seller must be allowed to sell, but never narrows the stock."""
    if not frappe.db.get_single_value("GSF Settings", "enabled"):
        raise GSFError("GSF is not enabled", "GSF_NOT_ENABLED")
    if not frappe.db.exists("GSF Company Group", {"name": request.company_group, "enabled": 1}):
        raise GSFError(f"Company group {request.company_group} is not active", "GROUP_NOT_FOUND")
    if frappe.db.get_value("GSF Physical Location", request.physical_location, "disabled"):
        raise GSFError(
            f"Location {request.physical_location} is disabled", "LOCATION_NOT_ACTIVE"
        )
    seller = frappe.db.exists(
        "GSF Group Member",
        {"parent": request.company_group, "company": request.seller_company, "enabled": 1, "can_sell_stock": 1},
    )
    if not seller:
        raise GSFError(
            f"{request.seller_company} may not sell in {request.company_group}", "SELLER_NOT_ALLOWED"
        )
    if not frappe.db.exists(
        "GSF Location Company Binding",
        {
            "company_group": request.company_group,
            "physical_location": request.physical_location,
            "company": request.seller_company,
            "enabled": 1,
            "can_sell": 1,
        },
    ):
        raise GSFError(
            f"{request.seller_company} has no active selling binding at {request.physical_location}",
            "SELLER_NOT_ALLOWED",
        )


def _existing_allocation(request: ReservationRequest, fingerprint: str) -> Any:
    """§13.4: the same key must mean the same request, or nothing at all."""
    row = frappe.db.get_value(
        "GSF Allocation",
        {"idempotency_key": request.idempotency_key},
        ["name", "request_fingerprint", "status", "expires_at"],
        as_dict=True,
    )
    if not row:
        return None
    if row.request_fingerprint != fingerprint:
        raise GSFError(
            f"Idempotency key {request.idempotency_key} was already used for a different request",
            "IDEMPOTENCY_CONFLICT",
        )
    if row.status in LIVE_ALLOCATION_STATUSES and _is_expired(row.expires_at):
        # Refuse, but do not expire it here: this call has taken no locks yet,
        # and a write on the read path would be undone by the caller's rollback
        # anyway. `expire_due_allocations` owns that transition.
        raise GSFError(
            f"Allocation {row.name} expired; a new reservation needs a new key",
            "ALLOCATION_EXPIRED",
        )
    return frappe.get_doc("GSF Allocation", row.name)


def _is_expired(expires_at: Any) -> bool:
    return bool(expires_at) and get_datetime(expires_at) <= now_datetime()


def _lock_scope(request: ReservationRequest) -> str:
    """§13.2 level 2 — every reservation in one FIFO scope queues here."""
    name = scope_lock_identity(
        company_group=request.company_group,
        physical_location=request.physical_location,
        item_code=request.item_code,
    )
    if not _lock_scope_row(name, required=False):
        try:
            frappe.get_doc(
                {
                    "doctype": "GSF Scope Lock",
                    "company_group": request.company_group,
                    "physical_location": request.physical_location,
                    "item_code": request.item_code,
                }
            ).insert(ignore_permissions=True, set_name=name)
        except frappe.DuplicateEntryError:
            # Another request created the same scope row first; that is the row
            # we wanted, so take its lock rather than retry the whole attempt.
            pass
        _lock_scope_row(name)
    return name


def _lock_scope_row(name: str, *, required: bool = True) -> bool:
    rows = frappe.db.sql(
        "select name from `tabGSF Scope Lock` where name = %s for update",
        (name,),
    )
    if not rows and required:
        raise GSFError(f"FIFO scope lock {name} does not exist", "ALLOCATION_CONFLICT")
    return bool(rows)


def _plan(request: ReservationRequest) -> list:
    """§12.4 selection. Reads current state; the locks above make it stable."""
    pools = source_warehouses(
        company_group=request.company_group, physical_location=request.physical_location
    )
    allowed = frozenset(request.allowed_warehouses) & frozenset(pools)
    if not allowed:
        raise GSFError(
            "No GSF OWN Pool of an active sourcing member matches the request",
            "WAREHOUSE_BINDING_MISSING",
        )
    from erpnext_ua.consignment_and_commission.services.allocation import (
        AllocationError,
        InsufficientStockError,
    )

    from .stock_domain_runtime import plan_stock_domains, require_gsf_only

    try:
        return require_gsf_only(
            plan_stock_domains(
                company_group=request.company_group,
                physical_location=request.physical_location,
                seller_company=request.seller_company,
                item_code=request.item_code,
                qty=Decimal(str(request.qty)),
                serial_no=request.serial_no,
                batch_no=request.batch_no,
                allowed_gsf_warehouses=allowed,
            )
        )
    except InsufficientStockError as error:
        raise GSFError(str(error), "INSUFFICIENT_GLOBAL_STOCK") from error
    except AllocationError as error:
        raise GSFError(str(error), "ALLOCATION_CONFLICT") from error


def _new_allocation(
    request: ReservationRequest, *, fingerprint: str, slices: list, reserved_at, expires_at
) -> Any:
    """§13.2 level 3. Inserted PENDING with its window already set, so the move
    to RESERVED changes only the status and never an immutable field."""
    pools = source_warehouses(
        company_group=request.company_group, physical_location=request.physical_location
    )
    allocation = frappe.get_doc(
        {
            "doctype": "GSF Allocation",
            "status": ALLOCATION_PENDING,
            "idempotency_key": request.idempotency_key,
            "request_fingerprint": fingerprint,
            "company_group": request.company_group,
            "physical_location": request.physical_location,
            "seller_company": request.seller_company,
            "item_code": request.item_code,
            "requested_qty": float(request.qty),
            "allocated_qty": float(sum((row.qty for row in slices), Decimal("0"))),
            "allowed_warehouses": "\n".join(sorted(request.allowed_warehouses)),
            "serial_no": request.serial_no,
            "batch_no": request.batch_no,
            "item_policy_snapshot": request.item_policy,
            "external_row_id": request.external_row_id,
            "posting_date": request.posting_date,
            "checkout": request.checkout,
            "reserved_at": reserved_at,
            "expires_at": expires_at,
            "slices": [
                {
                    "sequence": row.sequence,
                    "stock_layer": row.lot_name,
                    "source_company": pools[row.warehouse],
                    "source_warehouse": row.warehouse,
                    "physical_location": request.physical_location,
                    "qty": float(row.qty),
                    "original_fifo_datetime": row.fifo_datetime,
                    "origin_document": row.receipt_name,
                    "origin_row_index": row.receipt_row_index,
                    "batch_no": row.batch_no,
                    "serial_no": row.serial_no,
                    "source_balance_key": balance_identity(
                        stock_layer=row.lot_name,
                        company=pools[row.warehouse],
                        warehouse=row.warehouse,
                    ),
                    # §14.3: a slice owned by anyone but the seller is what
                    # triggers reallocation later. Recorded now, acted on in
                    # Phase 4.
                    "requires_reallocation": int(pools[row.warehouse] != request.seller_company),
                }
                for row in slices
            ],
        }
    )
    with _service_write():
        allocation.insert(
            ignore_permissions=True,
            set_name=allocation_identity(
                idempotency_key=request.idempotency_key, fingerprint=fingerprint
            ),
        )
    return allocation


def _hold_positions(request: ReservationRequest, slices: list) -> None:
    """§13.2 levels 4 and 5, then the guarded increment."""
    pools = source_warehouses(
        company_group=request.company_group, physical_location=request.physical_location
    )
    wanted: dict[tuple[str, str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for row in slices:
        wanted[(row.lot_name, pools[row.warehouse], row.warehouse)] += row.qty

    # Level 4: layers, by name.
    layer_names = sorted({key[0] for key in wanted})
    locked = frappe.db.sql(
        """
        select name, layer_status, company_group, physical_location, item_code
        from `tabGSF Stock Layer` where name in %s order by name for update
        """,
        (tuple(layer_names),),
        as_dict=True,
    )
    by_name = {row.name: row for row in locked}
    for name in layer_names:
        layer = by_name.get(name)
        if not layer:
            raise GSFError(f"Layer {name} disappeared during allocation", "ALLOCATION_CONFLICT")
        drifted = (
            layer.layer_status != "OPEN"
            or layer.company_group != request.company_group
            or layer.physical_location != request.physical_location
            or layer.item_code != request.item_code
        )
        if drifted:
            raise GSFError(
                f"Layer {name} changed during allocation", "ALLOCATION_CONFLICT"
            )

    # Level 5: balances, by company, warehouse, layer.
    for stock_layer, company, warehouse in sorted(wanted, key=lambda key: (key[1], key[2], key[0])):
        _hold_one(
            stock_layer=stock_layer,
            company=company,
            warehouse=warehouse,
            qty=wanted[(stock_layer, company, warehouse)],
        )


def _hold_one(*, stock_layer: str, company: str, warehouse: str, qty: Decimal) -> None:
    name = _ensure_balance(stock_layer=stock_layer, company=company, warehouse=warehouse)
    frappe.db.sql("select name from `tabGSF Layer Balance` where name = %s for update", (name,))

    ledger = ledger_balance(stock_layer=stock_layer, warehouse=warehouse)
    frappe.db.sql(
        """
        update `tabGSF Layer Balance`
        set reserved_qty_cache = reserved_qty_cache + %(qty)s,
            actual_qty_cache = %(ledger)s,
            -- MariaDB evaluates SET left to right, so both columns above
            -- already hold their new values here. Deriving the third from the
            -- other two is the only spelling that does not double-count.
            available_qty_cache = actual_qty_cache - reserved_qty_cache,
            last_reconciled_at = %(now)s,
            integrity_status = 'OK',
            modified = %(now)s
        where name = %(name)s
          and reserved_qty_cache + %(qty)s <= %(ledger)s
        """,
        {"qty": float(qty), "ledger": float(ledger), "name": name, "now": now_datetime()},
    )
    if int(frappe.db.sql("select row_count()")[0][0]) != 1:
        raise GSFError(
            f"Layer {stock_layer} no longer has {qty} allocatable in {warehouse}",
            "ALLOCATION_CONFLICT",
        )


def ledger_balance(*, stock_layer: str, warehouse: str) -> Decimal:
    """The only trustworthy quantity: what the ledger says right now (§9.10)."""
    value = frappe.db.sql(
        """
        select sum(actual_qty) from `tabStock Ledger Entry`
        where gsf_stock_layer = %s and warehouse = %s and is_cancelled = 0
        """,
        (stock_layer, warehouse),
    )[0][0]
    return Decimal(str(value or 0))


def _ensure_balance(*, stock_layer: str, company: str, warehouse: str) -> str:
    name = balance_identity(stock_layer=stock_layer, company=company, warehouse=warehouse)
    if frappe.db.exists("GSF Layer Balance", name):
        return name
    try:
        frappe.get_doc(
            {
                "doctype": "GSF Layer Balance",
                "stock_layer": stock_layer,
                "company": company,
                "warehouse": warehouse,
                "physical_location": frappe.db.get_value(
                    "GSF Stock Layer", stock_layer, "physical_location"
                ),
                "actual_qty_cache": float(
                    ledger_balance(stock_layer=stock_layer, warehouse=warehouse)
                ),
            }
        ).insert(ignore_permissions=True)
    except frappe.DuplicateEntryError:
        pass
    return name


def release_positions(allocation: Any) -> None:
    """Give the held quantity back exactly once, in the §13.2 order it was taken.

    Once, because a position's `reserved_qty_cache` is one number shared by
    every allocation holding it: a second decrement hands back units another
    allocation still owns. Preparation releases the source hold when the stock
    physically leaves (the reservation has become real stage stock by then), and
    the terminal transitions must not repeat it.
    """
    if allocation.get("positions_released"):
        return
    for row in sorted(
        allocation.slices, key=lambda row: (row.source_company, row.source_warehouse, row.stock_layer)
    ):
        name = balance_identity(
            stock_layer=row.stock_layer, company=row.source_company, warehouse=row.source_warehouse
        )
        if not frappe.db.exists("GSF Layer Balance", name):
            continue
        frappe.db.sql("select name from `tabGSF Layer Balance` where name = %s for update", (name,))
        frappe.db.sql(
            """
            update `tabGSF Layer Balance`
            set reserved_qty_cache = greatest(reserved_qty_cache - %(qty)s, 0),
                available_qty_cache = actual_qty_cache - reserved_qty_cache,
                modified = %(now)s
            where name = %(name)s
            """,
            {"qty": float(row.qty or 0), "name": name, "now": now_datetime()},
        )
    allocation.positions_released = 1


def _finish(allocation_name: str, *, status: str, **fields) -> Any:
    # Release/consume/expire touches the same balance rows as reserve. It must
    # therefore enter through the same level-2 scope lock before taking the
    # allocation and balance locks. Without this, concurrent releases invert
    # §13.2 and MariaDB can raise error 1020 after the reservation was already
    # committed, leaving a live hold behind.
    scope = frappe.db.get_value(
        "GSF Allocation",
        allocation_name,
        ["company_group", "physical_location", "item_code"],
        as_dict=True,
    )
    if not scope:
        raise GSFError(f"Allocation {allocation_name} does not exist", "ALLOCATION_CONFLICT")
    if not frappe.db.transaction_writes:
        # As in reserve(), discard the coordinate lookup's consistent-read
        # snapshot before the scope lock becomes the first write-path read.
        frappe.db.rollback()
    _lock_scope_row(
        scope_lock_identity(
            company_group=scope.company_group,
            physical_location=scope.physical_location,
            item_code=scope.item_code,
        )
    )
    rows = frappe.db.sql(
        "select status from `tabGSF Allocation` where name = %s for update",
        (allocation_name,),
        as_dict=True,
    )
    if not rows:
        raise GSFError(f"Allocation {allocation_name} does not exist", "ALLOCATION_CONFLICT")
    if rows[0].status == status:
        # Already there, so return it untouched. Falling through would release
        # the positions a second time, and since a position's reservation is a
        # single number shared by every allocation holding it, the second
        # decrement would give away stock another allocation still holds.
        return frappe.get_doc("GSF Allocation", allocation_name)
    validate_allocation_transition(rows[0].status, status)

    allocation = frappe.get_doc("GSF Allocation", allocation_name)
    release_positions(allocation)
    allocation.status = status
    for fieldname, value in fields.items():
        allocation.set(fieldname, value)
    with _service_write():
        allocation.save(ignore_permissions=True)
    return allocation


def release_allocation(allocation_name: str, *, reason: str) -> Any:
    """§13.3. Releasing a staged allocation is flagged, not silently equated
    with releasing a merely reserved one — the stock is already in a lane."""
    status = frappe.db.get_value("GSF Allocation", allocation_name, "status")
    allocation = _finish(
        allocation_name,
        status=ALLOCATION_RELEASED,
        failure_code="MANUAL_REVIEW_REQUIRED" if needs_compensation(status) else None,
        failure_message=reason,
    )
    return allocation


def consume_allocation(
    allocation_name: str, *, consumer_doctype: str, consumer_document: str
) -> Any:
    """The reservation is over because the stock actually left."""
    return _finish(
        allocation_name,
        status=ALLOCATION_CONSUMED,
        consumer_doctype=consumer_doctype,
        consumer_document=consumer_document,
    )


def expire_allocation(allocation_name: str) -> Any:
    status = frappe.db.get_value("GSF Allocation", allocation_name, "status")
    return _finish(
        allocation_name,
        status=ALLOCATION_EXPIRED,
        failure_code="ALLOCATION_EXPIRED",
        failure_message=(
            "Expired after stage preparation started; compensation required"
            if needs_compensation(status)
            else "Expired before use"
        ),
    )


def expire_due_allocations(limit: int = 500) -> int:
    """Release expired reservations in bounded scheduler batches."""
    due = frappe.get_all(
        "GSF Allocation",
        filters={"status": ("in", LIVE_ALLOCATION_STATUSES), "expires_at": ("<=", now_datetime())},
        order_by="expires_at asc, name asc",
        limit=limit,
        pluck="name",
    )
    for name in due:
        expire_allocation(name)
    return len(due)
