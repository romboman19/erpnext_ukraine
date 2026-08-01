"""The checkout saga: reserve, prepare, sell, and undo when any of it fails.

Every step this walks already exists and was proved on its own. What the saga
adds is the thing none of them can have individually — a record of how far the
sale got, written down before each step and readable afterwards by something
that was not running when the step failed.

That matters because of one asymmetry. Up to the invoice, a failure can roll
back: §14.6 puts the whole preparation and the sale in one transaction. After
it, an external fiscal receipt may exist, and no rollback reaches that. So the
saga's job is to know exactly which side of that line it is on, and to reverse
rather than rewind once it is past it (§23.2).

Every step is idempotent, so `resume` can re-run the one that failed without
double-posting: reservation is keyed, preparation refuses an allocation that is
not still reserved, the sale refuses one that is not prepared, and compensation
is keyed by movement.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import frappe
from frappe.utils import now_datetime

from ..doctype.gsf_checkout.gsf_checkout import WRITE_FLAG
from . import checkout_states as states
from .allocations import release_allocation, reserve
from .compensation import compensate
from .domain import GSFError
from .reallocation import prepare
from .reservation import ReservationRequest
from .sale import SaleLine, sell
from .staging import acquire_lane, release_lane


@contextmanager
def _service_write():
    setattr(frappe.flags, WRITE_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, WRITE_FLAG, False)


@dataclass(frozen=True, slots=True)
class CheckoutLine:
    item_code: str
    qty: Decimal
    rate: Decimal
    external_row_id: str | None = None
    uom: str | None = None
    barcode: str | None = None
    serial_no: str | None = None
    batch_no: str | None = None
    discount_amount: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    idempotency_key: str
    company_group: str
    physical_location: str
    seller_company: str
    customer: str
    lines: tuple[CheckoutLine, ...]
    external_order_doctype: str | None = None
    external_order_name: str | None = None
    requires_fiscalization: bool = False


def open_checkout(request: CheckoutRequest) -> Any:
    """Record the intent before anything is reserved (§23, `DRAFT`).

    The row exists first so that a crash between here and the first reservation
    leaves something to find. A saga that only becomes visible once it has
    already taken stock is a saga that can lose stock.
    """
    if not request.lines:
        raise GSFError("A checkout needs at least one line", "MANUAL_REVIEW_REQUIRED")

    fingerprint = _fingerprint(request)
    existing = frappe.db.get_value(
        "GSF Checkout",
        {"idempotency_key": request.idempotency_key},
        ["name", "request_fingerprint"],
        as_dict=True,
    )
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise GSFError(
                f"Checkout key {request.idempotency_key} was already used for a different basket",
                "IDEMPOTENCY_CONFLICT",
            )
        return frappe.get_doc("GSF Checkout", existing.name)

    with _service_write():
        return frappe.get_doc(
            {
                "doctype": "GSF Checkout",
                "status": states.DRAFT,
                "idempotency_key": request.idempotency_key,
                "request_fingerprint": fingerprint,
                "company_group": request.company_group,
                "physical_location": request.physical_location,
                "seller_company": request.seller_company,
                "customer": request.customer,
                "external_order_doctype": request.external_order_doctype,
                "external_order_name": request.external_order_name,
                "fiscal_state": "PENDING" if request.requires_fiscalization else "NOT_REQUIRED",
                "posting_datetime": now_datetime(),
                "lines": [
                    {
                        "item_code": line.item_code,
                        "qty": float(line.qty),
                        "rate": float(line.rate),
                        "external_row_id": line.external_row_id,
                        "uom": line.uom,
                        "barcode": line.barcode,
                        "serial_no": line.serial_no,
                        "batch_no": line.batch_no,
                        "discount_amount": float(line.discount_amount),
                    }
                    for line in request.lines
                ],
            }
        ).insert(ignore_permissions=True)


def _fingerprint(request: CheckoutRequest) -> str:
    payload = {
        "company_group": request.company_group,
        "physical_location": request.physical_location,
        "seller_company": request.seller_company,
        "customer": request.customer,
        "lines": [
            {
                "item_code": line.item_code,
                "qty": str(Decimal(str(line.qty)).normalize()),
                "rate": str(Decimal(str(line.rate)).normalize()),
                "external_row_id": line.external_row_id,
                "uom": line.uom,
                "barcode": line.barcode,
                "serial_no": line.serial_no,
                "batch_no": line.batch_no,
                "discount_amount": str(Decimal(str(line.discount_amount)).normalize()),
            }
            for line in request.lines
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def run(checkout_name: str, *, stop_at: str | None = None) -> Any:
    """Walk the saga as far as it will go, from wherever it currently is.

    Safe to call again after a failure: `next_step` reads the recorded state,
    and every step refuses to repeat work it already did.

    `stop_at` halts once that state is reached. A till may legitimately want the
    stock staged before the customer has paid, and stopping there is a normal
    outcome rather than a failure — the checkout simply waits, holding its lane.
    """
    checkout = frappe.get_doc("GSF Checkout", checkout_name)
    guard = 0
    while True:
        guard += 1
        if guard > len(states.TRANSITIONS):
            raise GSFError(
                f"Checkout {checkout_name} is not converging from {checkout.status}",
                "MANUAL_REVIEW_REQUIRED",
            )
        if stop_at and checkout.status == stop_at:
            return checkout
        step = states.next_step(checkout.status)
        if not step:
            return checkout
        before = checkout.status
        checkout = _STEPS[step](checkout)
        if checkout.status == before:
            # A step that changed nothing means the saga is waiting on
            # something outside itself — fiscalization, or an operator.
            return checkout


def _reserve(checkout: Any) -> Any:
    """§23: `DRAFT → RESERVING → RESERVED`, one allocation per basket line.

    This step commits between lines, and that is deliberate rather than
    convenient. §13.1 requires each reservation to begin a transaction of its
    own, so its locks are not held across unrelated work; and §23 requires the
    saga to be findable after a crash, which a row that was never committed is
    not. Both point the same way here.

    §14.6's ban on committing inside a service still holds where it was aimed —
    at preparation and sale, which stay in one transaction below so that a
    failed invoice takes the stock movements back with it.
    """
    _move(checkout, states.RESERVING)
    lane = checkout.staging_lane or acquire_lane(
        company=checkout.seller_company,
        physical_location=checkout.physical_location,
        checkout=checkout.name,
    )
    # The lane is claimed before the first reservation so that a basket cannot
    # hold stock it will never have anywhere to stage. It is claimed by data,
    # not by a row lock, so committing below does not give it up.
    checkout.staging_lane = lane
    _save(checkout)

    for line in checkout.lines:
        if line.allocation:
            continue
        frappe.db.commit()
        allocation = reserve(
            ReservationRequest(
                idempotency_key=f"{checkout.idempotency_key}:{line.idx}",
                company_group=checkout.company_group,
                physical_location=checkout.physical_location,
                seller_company=checkout.seller_company,
                item_code=line.item_code,
                qty=Decimal(str(line.qty)),
                allowed_warehouses=_pools(checkout),
                serial_no=line.serial_no,
                batch_no=line.batch_no,
                external_row_id=line.external_row_id,
                checkout=checkout.name,
            )
        )
        line.allocation = allocation.name
        _save(checkout)
    _move(checkout, states.RESERVED, stock_state="RESERVED")
    return checkout


def _prepare(checkout: Any) -> Any:
    """§23: `RESERVED → PREPARING_STOCK → STOCK_PREPARED`."""
    _move(checkout, states.PREPARING_STOCK)
    reallocations = _recorded_reallocations(checkout)
    for line in checkout.lines:
        if line.allocation in reallocations:
            continue
        reallocation = prepare(
            line.allocation, checkout=checkout.name, staging_lane=checkout.staging_lane
        )
        reallocations[line.allocation] = reallocation.name
    _move(
        checkout,
        states.STOCK_PREPARED,
        stock_state="PREPARED",
        reallocations=json.dumps(reallocations),
    )
    return checkout


def _sell(checkout: Any) -> Any:
    """§23: `STOCK_PREPARED → ERP_SALE_SUBMITTED`, the last reversible step."""
    invoice = sell(
        [
            SaleLine(
                allocation=line.allocation,
                rate=Decimal(str(line.rate)),
                uom=line.uom,
                barcode=line.barcode,
                discount_amount=Decimal(str(line.discount_amount or 0)),
            )
            for line in checkout.lines
        ],
        customer=checkout.customer,
        checkout=checkout.name,
        invoice_values=_external_invoice_values(checkout),
    )
    _move(
        checkout,
        states.ERP_SALE_SUBMITTED,
        sales_invoice=invoice.name,
        erp_sale_state="SUBMITTED",
        stock_state="CONSUMED",
    )
    return checkout


def _external_invoice_values(checkout: Any) -> dict[str, Any]:
    if checkout.external_order_doctype != "POS Order" or not checkout.external_order_name:
        return {}
    from .pos_ua import sale_invoice_values

    return sale_invoice_values(checkout.external_order_name)


def _complete(checkout: Any) -> Any:
    """Either finished, or handed to the fiscal route that owns it (ADR-012)."""
    if checkout.fiscal_state == "PENDING":
        _move(checkout, states.FISCAL_PENDING)
        return checkout
    _move(checkout, states.COMPLETED, completed_at=now_datetime())
    return checkout


def _await_fiscal(checkout: Any) -> Any:
    """GSF does not fiscalize; `POS Order` does (ADR-012).

    So this reads the outcome rather than driving it, and returns unchanged
    while it is still pending — which is what stops `run` from spinning.
    """
    if checkout.fiscal_state == "DONE":
        _move(checkout, states.COMPLETED, completed_at=now_datetime())
    elif checkout.fiscal_state in ("FAILED", "UNCERTAIN"):
        _move(
            checkout,
            states.MANUAL_REVIEW,
            failure_code="FISCALIZATION_UNCERTAIN",
            manual_review_reason=f"Fiscal state is {checkout.fiscal_state}",
        )
    return checkout


def _compensate(checkout: Any) -> Any:
    """§23.2: reverse what was posted, in the reverse order it was posted."""
    reallocations = _recorded_reallocations(checkout)
    for name in reversed(list(reallocations.values())):
        compensate(name, reason=f"checkout {checkout.name} compensating")
    for line in checkout.lines:
        if line.allocation:
            _release_quietly(line.allocation)
    if checkout.staging_lane:
        _release_lane_quietly(checkout)
    _move(checkout, states.COMPENSATED, stock_state="COMPENSATED")
    return checkout


_STEPS = {
    "reserve": _reserve,
    "prepare": _prepare,
    "sell": _sell,
    "complete": _complete,
    "await_fiscal": _await_fiscal,
    "compensate": _compensate,
}


def abort(checkout_name: str, *, reason: str) -> Any:
    """Stop a checkout deliberately, choosing rollback or compensation by state.

    §23.2 decides which: before anything was staged there is nothing posted to
    reverse, and after the invoice exists it is too late to abort at all — that
    case is a return, not a cancellation.
    """
    checkout = frappe.get_doc("GSF Checkout", checkout_name)
    if checkout.status in (states.CANCELLED, states.COMPENSATED):
        # Already stopped. Asking again is a retry, not a mistake.
        return checkout
    if not states.is_reversible(checkout.status):
        raise GSFError(
            f"Checkout {checkout_name} is {checkout.status}; use the return flow, not an abort",
            "MANUAL_REVIEW_REQUIRED",
        )

    if states.needs_compensation(checkout.status):
        _move(checkout, states.COMPENSATING, manual_review_reason=reason)
        return _compensate(checkout)

    for line in checkout.lines:
        if line.allocation:
            _release_quietly(line.allocation)
    if checkout.staging_lane:
        _release_lane_quietly(checkout)
    _move(checkout, states.CANCELLED, manual_review_reason=reason)
    return checkout


def fail(checkout_name: str, *, code: str, reason: str) -> Any:
    """Record a failure without deciding what to do about it."""
    checkout = frappe.get_doc("GSF Checkout", checkout_name)
    _move(checkout, states.FAILED, failure_code=code, manual_review_reason=reason)
    return checkout


def record_fiscal_result(
    checkout_name: str, *, fiscal_state: str, prro_receipt: str | None = None
) -> Any:
    """Mirror the POS-owned fiscal result and let the checkout finish."""
    if fiscal_state not in {"DONE", "FAILED", "UNCERTAIN"}:
        raise GSFError(f"Unsupported fiscal state {fiscal_state}", "MANUAL_REVIEW_REQUIRED")
    checkout = frappe.get_doc("GSF Checkout", checkout_name)
    checkout.fiscal_state = fiscal_state
    checkout.prro_receipt_id = prro_receipt
    _save(checkout)
    if fiscal_state == "DONE" and checkout.status == states.MANUAL_REVIEW:
        _move(checkout, states.COMPLETED, completed_at=now_datetime())
        return checkout
    return run(checkout.name)


def _save(checkout: Any) -> None:
    """Persist the saga's own bookkeeping without changing its state."""
    with _service_write():
        checkout.save(ignore_permissions=True)


def _move(checkout: Any, status: str, **fields) -> None:
    states.validate_transition(checkout.status, status)
    checkout.status = status
    for fieldname, value in fields.items():
        checkout.set(fieldname, value)
    with _service_write():
        checkout.save(ignore_permissions=True)


def _pools(checkout: Any) -> frozenset[str]:
    from .candidates import source_warehouses

    return frozenset(
        source_warehouses(
            company_group=checkout.company_group,
            physical_location=checkout.physical_location,
        )
    )


def _recorded_reallocations(checkout: Any) -> dict[str, str]:
    """Allocation → reallocation, so a resumed preparation skips what it did."""
    return json.loads(checkout.reallocations or "{}")


def _release_quietly(allocation: str) -> None:
    """An allocation that is already finished is not an error to release."""
    try:
        release_allocation(allocation, reason="checkout aborted")
    except GSFError:
        pass


def _release_lane_quietly(checkout: Any) -> None:
    """§44 forbids cleaning a dirty lane, so a refusal is recorded, not swallowed."""
    try:
        release_lane(checkout.staging_lane, checkout=checkout.name)
    except GSFError as error:
        with _service_write():
            checkout.failure_code = error.code
            checkout.manual_review_reason = (
                f"{checkout.manual_review_reason or ''}\n{error}".strip()
            )
            checkout.save(ignore_permissions=True)
