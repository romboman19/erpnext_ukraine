"""Persistent split POS routes, retries, print jobs and safe compensation."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from typing import Any

from ..doctype.cc_pos_checkout.cc_pos_checkout import WRITE_FLAG as CHECKOUT_WRITE_FLAG
from ..doctype.cc_pos_print_job.cc_pos_print_job import WRITE_FLAG as PRINT_WRITE_FLAG
from ..doctype.cc_pos_route.cc_pos_route import WRITE_FLAG as ROUTE_WRITE_FLAG
from ..services.pos_checkout import (
    POSCheckoutError,
    POSCheckoutRequest,
    pos_checkout_fingerprint,
    validate_pos_checkout_request,
)
from ..services.pos_saga import CheckoutGroup, RouteKey, allocate_payment_plan
from ..services.sale import ManagedSaleLine, ManagedSaleRequest
from ..setup.ownership_dimension import POS_CHECKOUT_FIELD, POS_ORDER_FIELD, POS_ROUTE_FIELD
from .reservations import release_allocation
from .sales_invoice import create_sales_invoice_from_allocations


@contextmanager
def _write_flag(frappe: Any, flag: str):
    previous = getattr(frappe.flags, flag, False)
    setattr(frappe.flags, flag, True)
    try:
        yield
    finally:
        setattr(frappe.flags, flag, previous)


def _currency_quantum(frappe: Any) -> Decimal:
    precision = int(frappe.db.get_single_value("System Settings", "currency_precision") or 2)
    return Decimal("1").scaleb(-precision)


def _expected_fiscal_route(
    *,
    fiscal_checkout: bool,
    relationship_model: str,
    fiscal_policy: str | None,
) -> str:
    if not fiscal_checkout:
        return "NON_FISCAL"
    if fiscal_policy == "FISCAL":
        return "FISCAL"
    if fiscal_policy == "NON_FISCAL":
        return "NON_FISCAL"
    return "NON_FISCAL" if relationship_model == "COMMISSION" else "FISCAL"


def _allocation_fiscal_policies(frappe: Any, allocation: Any) -> list[tuple[str, str | None]]:
    result = []
    for allocation_slice in allocation.slices:
        contract = frappe.db.get_value("CC Stock Lot", allocation_slice.stock_lot, "contract")
        policy = (
            frappe.db.get_value("CC Contract", contract, "fiscal_policy")
            if contract
            else allocation.fiscal_policy
        )
        result.append((allocation_slice.relationship_model, policy or "AUTO"))
    return result


def _existing_checkout(frappe: Any, request: POSCheckoutRequest, fingerprint: str) -> Any | None:
    name = frappe.db.get_value(
        "CC POS Checkout",
        {"idempotency_key": request.idempotency_key},
        "name",
    )
    if not name:
        return None
    checkout = frappe.get_doc("CC POS Checkout", name)
    if checkout.request_fingerprint != fingerprint:
        raise POSCheckoutError(
            f"POS checkout idempotency key {request.idempotency_key!r} belongs to another request"
        )
    return checkout


def prepare_pos_checkout(request: POSCheckoutRequest) -> Any:
    """Persist an idempotent checkout plan from already committed reservations."""
    import frappe

    if frappe.db.transaction_writes:
        raise POSCheckoutError("POS checkout preparation must start before unrelated writes")
    validate_pos_checkout_request(request)
    fingerprint = pos_checkout_fingerprint(request)
    existing = _existing_checkout(frappe, request, fingerprint)
    if existing:
        return existing
    if not frappe.db.exists("Customer", request.customer):
        raise POSCheckoutError(f"Customer {request.customer} does not exist")
    if not frappe.db.exists("Currency", request.currency):
        raise POSCheckoutError(f"Currency {request.currency} does not exist")
    missing_modes = [
        tender.mode_of_payment
        for tender in request.tenders
        if not frappe.db.exists("Mode of Payment", tender.mode_of_payment)
    ]
    if missing_modes:
        raise POSCheckoutError(f"Unknown Mode of Payment: {', '.join(sorted(set(missing_modes)))}")
    quantum = _currency_quantum(frappe)
    all_names = sorted(
        line.allocation
        for route in request.routes
        for line in route.lines
    )
    placeholders = ", ".join(["%s"] * len(all_names))
    locked = frappe.db.sql(
        f"select name from `tabCC Allocation` where name in ({placeholders}) "
        "order by name for update",
        tuple(all_names),
        as_dict=True,
    )
    if {row.name for row in locked} != set(all_names):
        raise POSCheckoutError("One or more POS allocations do not exist")

    route_rows = []
    groups = []
    checkout_total = Decimal("0")
    for route_request in request.routes:
        location = frappe.db.get_value(
            "CC Location",
            route_request.location,
            ["company", "disabled", "legal_entity_type", "legal_entity_name"],
            as_dict=True,
        )
        if (
            not location
            or location.disabled
            or location.company != route_request.company
            or location.legal_entity_type != route_request.legal_entity_type
            or location.legal_entity_name != route_request.legal_entity_name
        ):
            raise POSCheckoutError(f"POS route {route_request.group_id} has invalid location/entity")
        company_currency = frappe.get_cached_value(
            "Company",
            route_request.company,
            "default_currency",
        )
        if request.currency == company_currency and Decimal(str(request.conversion_rate)) != 1:
            raise POSCheckoutError("Company-currency POS checkout requires conversion rate 1")
        lines = []
        route_total = Decimal("0")
        for line in route_request.lines:
            allocation = frappe.get_doc("CC Allocation", line.allocation)
            if allocation.status != "RESERVED":
                raise POSCheckoutError(
                    f"CC Allocation {allocation.name} must be RESERVED, not {allocation.status}"
                )
            if (
                allocation.company != route_request.company
                or allocation.location != route_request.location
            ):
                raise POSCheckoutError(f"CC Allocation {allocation.name} belongs to another route")
            fiscal_coordinates = _allocation_fiscal_policies(frappe, allocation)
            expected_routes = {
                _expected_fiscal_route(
                    fiscal_checkout=request.fiscal_checkout,
                    relationship_model=model,
                    fiscal_policy=policy,
                )
                for model, policy in fiscal_coordinates
            }
            if expected_routes != {route_request.fiscal_route}:
                raise POSCheckoutError(
                    f"CC Allocation {allocation.name} violates fiscal split: {sorted(expected_routes)}"
                )
            qty = Decimal(str(allocation.requested_qty))
            rate = Decimal(str(line.rate))
            amount = (qty * rate).quantize(quantum, rounding=ROUND_HALF_UP)
            route_total += amount
            lines.append(
                {
                    "external_row_id": line.external_row_id,
                    "allocation": allocation.name,
                    "item_code": allocation.item_code,
                    "qty": qty,
                    "rate": rate,
                    "amount": amount,
                    "relationship_models": ",".join(
                        sorted({model for model, _policy in fiscal_coordinates})
                    ),
                }
            )
        checkout_total += route_total
        groups.append(
            CheckoutGroup(
                group_id=route_request.group_id,
                key=RouteKey(
                    route_request.company,
                    route_request.legal_entity_name,
                    route_request.fiscal_route,
                ),
                lines=(),
                total=route_total,
            )
        )
        route_rows.append((route_request, lines, route_total))
    payment_allocations = allocate_payment_plan(tuple(groups), list(request.tenders))
    payment_total = sum((Decimal(str(row.amount)) for row in request.tenders), Decimal("0"))
    if checkout_total != payment_total:
        raise POSCheckoutError("POS checkout route total differs from its payment plan")

    checkout_name = "CC-POS-" + sha256(
        f"{request.idempotency_key}:{fingerprint}".encode()
    ).hexdigest()[:20].upper()
    checkout = frappe.get_doc(
        {
            "doctype": "CC POS Checkout",
            "status": "PLANNED",
            "idempotency_key": request.idempotency_key,
            "request_fingerprint": fingerprint,
            "external_order_doctype": request.external_order_doctype,
            "external_order_name": request.external_order_name,
            "lookup_token": request.lookup_token,
            "customer": request.customer,
            "posting_date": request.posting_date,
            "fiscal_checkout": int(request.fiscal_checkout),
            "currency": request.currency,
            "conversion_rate": request.conversion_rate,
            "total_amount": checkout_total,
            "payment_total": payment_total,
            "routes_count": len(route_rows),
            "payment_state": "NONE",
        }
    )
    with _write_flag(frappe, CHECKOUT_WRITE_FLAG):
        checkout.insert(ignore_permissions=True, set_name=checkout_name)

    payments_by_group: dict[str, list[Any]] = {}
    for payment in payment_allocations:
        payments_by_group.setdefault(payment.group_id, []).append(payment)
    for route_request, lines, route_total in route_rows:
        route_name = "CC-POS-ROUTE-" + sha256(
            f"{checkout.name}:{route_request.group_id}".encode()
        ).hexdigest()[:20].upper()
        route = frappe.get_doc(
            {
                "doctype": "CC POS Route",
                "status": "PLANNED",
                "checkout": checkout.name,
                "group_id": route_request.group_id,
                "company": route_request.company,
                "location": route_request.location,
                "legal_entity_type": route_request.legal_entity_type,
                "legal_entity_name": route_request.legal_entity_name,
                "fiscal_route": route_request.fiscal_route,
                "customer": request.customer,
                "posting_date": request.posting_date,
                "currency": request.currency,
                "conversion_rate": request.conversion_rate,
                "total_amount": route_total,
                "items": lines,
                "payments": [
                    {
                        "tender_id": payment.tender_id,
                        "mode_of_payment": payment.mode_of_payment,
                        "amount": payment.amount,
                    }
                    for payment in payments_by_group.get(route_request.group_id, [])
                ],
            }
        )
        with _write_flag(frappe, ROUTE_WRITE_FLAG):
            route.insert(ignore_permissions=True, set_name=route_name)
    return checkout


def _route_invoice_request(route: Any, checkout: Any) -> ManagedSaleRequest:
    return ManagedSaleRequest(
        idempotency_key=f"{checkout.idempotency_key}:route:{route.group_id}",
        customer=route.customer,
        posting_date=str(route.posting_date),
        currency=route.currency,
        conversion_rate=Decimal(str(route.conversion_rate)),
        lines=tuple(
            ManagedSaleLine(row.allocation, Decimal(str(row.rate)))
            for row in route.items
        ),
        pos_checkout=checkout.name,
        pos_route=route.name,
        pos_order=checkout.external_order_name,
    )


def _create_print_job(frappe: Any, route: Any, invoice: Any) -> Any:
    existing = frappe.db.get_value("CC POS Print Job", {"route": route.name}, "name")
    if existing:
        return frappe.get_doc("CC POS Print Job", existing)
    print_kind = (
        "FISCAL_RECEIPT"
        if route.fiscal_route == "FISCAL"
        else "NON_FISCAL_GOODS_RECEIPT"
    )
    key = f"{route.name}:{print_kind}"
    job = frappe.get_doc(
        {
            "doctype": "CC POS Print Job",
            "status": "PENDING",
            "idempotency_key": key,
            "route": route.name,
            "sales_invoice": invoice.name,
            "print_kind": print_kind,
            "attempts": 0,
        }
    )
    name = "CC-PRINT-" + sha256(key.encode()).hexdigest()[:20].upper()
    with _write_flag(frappe, PRINT_WRITE_FLAG):
        job.insert(ignore_permissions=True, set_name=name)
    return job


def advance_pos_route(route_name: str) -> Any:
    """Idempotently create, submit and enqueue exactly one split route."""
    import frappe

    route = frappe.get_doc("CC POS Route", route_name)
    if route.status in {"PRINT_PENDING", "COMPLETED"}:
        return route
    if route.status in {"COMPENSATED", "FAILED"}:
        raise POSCheckoutError(f"POS route {route.name} cannot advance from {route.status}")
    checkout = frappe.get_doc("CC POS Checkout", route.checkout)
    if checkout.status in {"COMPENSATED", "MANUAL_REVIEW"}:
        raise POSCheckoutError(f"POS checkout {checkout.name} cannot advance")
    invoice = (
        frappe.get_doc("Sales Invoice", route.sales_invoice)
        if route.sales_invoice
        else create_sales_invoice_from_allocations(_route_invoice_request(route, checkout))
    )
    if not route.sales_invoice:
        frappe.db.set_value(
            "CC POS Route",
            route.name,
            {"sales_invoice": invoice.name, "status": "DRAFT"},
            update_modified=False,
        )
    if invoice.docstatus == 0:
        invoice.flags.ignore_permissions = True
        invoice.submit()
    if invoice.docstatus != 1:
        raise POSCheckoutError(f"POS route Sales Invoice {invoice.name} is not submitted")
    job = _create_print_job(frappe, route, invoice)
    frappe.db.set_value(
        "CC POS Route",
        route.name,
        {"status": "PRINT_PENDING", "print_job": job.name, "last_error": None},
        update_modified=False,
    )
    frappe.db.set_value(
        "CC POS Checkout",
        checkout.name,
        "status",
        "IN_PROGRESS",
        update_modified=False,
    )
    route.reload()
    return route


def validate_pos_route_invoice(doc: Any) -> None:
    import frappe

    route = frappe.get_doc("CC POS Route", doc.get(POS_ROUTE_FIELD))
    checkout = frappe.get_doc("CC POS Checkout", doc.get(POS_CHECKOUT_FIELD))
    expected = {
        POS_CHECKOUT_FIELD: route.checkout,
        POS_ROUTE_FIELD: route.name,
        POS_ORDER_FIELD: checkout.external_order_name,
        "company": route.company,
        "customer": route.customer,
        "posting_date": route.posting_date,
        "currency": route.currency,
        "conversion_rate": route.conversion_rate,
    }
    mismatches = [
        fieldname
        for fieldname, value in expected.items()
        if str(doc.get(fieldname) or "") != str(value or "")
    ]
    invoice_allocations = {
        row.cc_allocation for row in doc.items if row.cc_allocation
    }
    route_allocations = {row.allocation for row in route.items}
    if invoice_allocations != route_allocations:
        mismatches.append("allocations")
    if route.sales_invoice and route.sales_invoice != doc.name:
        mismatches.append("sales_invoice")
    if mismatches:
        frappe.throw(f"POS route invoice changed: {', '.join(dict.fromkeys(mismatches))}")


def _refresh_checkout_completion(frappe: Any, checkout_name: str) -> None:
    statuses = frappe.get_all(
        "CC POS Route",
        filters={"checkout": checkout_name},
        pluck="status",
    )
    status = "COMPLETED" if statuses and set(statuses) == {"COMPLETED"} else "IN_PROGRESS"
    frappe.db.set_value(
        "CC POS Checkout",
        checkout_name,
        "status",
        status,
        update_modified=False,
    )


def mark_print_job_succeeded(name: str, *, provider_reference: str = "") -> Any:
    import frappe
    from frappe.utils import now_datetime

    job = frappe.get_doc("CC POS Print Job", name)
    if job.status == "SUCCEEDED":
        return job
    if job.status == "CANCELLED":
        raise POSCheckoutError("Cancelled POS print job cannot succeed")
    attempts = int(job.attempts or 0) + 1
    frappe.db.set_value(
        "CC POS Print Job",
        job.name,
        {
            "status": "SUCCEEDED",
            "attempts": attempts,
            "provider_reference": provider_reference,
            "last_error": None,
            "completed_at": now_datetime(),
        },
        update_modified=False,
    )
    route = frappe.get_doc("CC POS Route", job.route)
    frappe.db.set_value(
        "CC POS Route",
        route.name,
        {"status": "COMPLETED", "last_error": None},
        update_modified=False,
    )
    _refresh_checkout_completion(frappe, route.checkout)
    job.reload()
    return job


def mark_print_job_failed(name: str, *, error: str) -> Any:
    import frappe

    if not error or not error.strip():
        raise POSCheckoutError("POS print failure requires an error message")
    job = frappe.get_doc("CC POS Print Job", name)
    if job.status in {"SUCCEEDED", "CANCELLED"}:
        raise POSCheckoutError(f"POS print job cannot fail from {job.status}")
    attempts = int(job.attempts or 0) + 1
    frappe.db.set_value(
        "CC POS Print Job",
        job.name,
        {"status": "FAILED", "attempts": attempts, "last_error": error.strip()},
        update_modified=False,
    )
    frappe.db.set_value(
        "CC POS Route",
        job.route,
        {"status": "FAILED", "last_error": error.strip()},
        update_modified=False,
    )
    job.reload()
    return job


def compensate_pos_checkout(name: str, *, reason: str) -> Any:
    """Reverse safe local state; captured/unknown money requires manual review."""
    import frappe

    if not reason or not reason.strip():
        raise POSCheckoutError("POS compensation requires a reason")
    checkout = frappe.get_doc("CC POS Checkout", name)
    if checkout.status == "COMPENSATED":
        return checkout
    if checkout.payment_state in {"CAPTURED", "UNKNOWN"}:
        frappe.db.set_value(
            "CC POS Checkout",
            checkout.name,
            {"status": "MANUAL_REVIEW", "last_error": reason.strip()},
            update_modified=False,
        )
        checkout.reload()
        return checkout
    succeeded_print = frappe.db.sql(
        """
        select job.name
        from `tabCC POS Print Job` job
        inner join `tabCC POS Route` route on route.name = job.route
        where route.checkout = %s and job.status = 'SUCCEEDED'
        limit 1
        """,
        (checkout.name,),
    )
    if succeeded_print:
        frappe.db.set_value(
            "CC POS Checkout",
            checkout.name,
            {"status": "MANUAL_REVIEW", "last_error": reason.strip()},
            update_modified=False,
        )
        checkout.reload()
        return checkout
    frappe.db.set_value(
        "CC POS Checkout",
        checkout.name,
        "status",
        "COMPENSATING",
        update_modified=False,
    )
    routes = frappe.get_all(
        "CC POS Route",
        filters={"checkout": checkout.name},
        pluck="name",
        order_by="creation desc, name desc",
    )
    for route_name in routes:
        route = frappe.get_doc("CC POS Route", route_name)
        if route.sales_invoice and frappe.db.exists("Sales Invoice", route.sales_invoice):
            invoice = frappe.get_doc("Sales Invoice", route.sales_invoice)
            if invoice.docstatus == 1:
                invoice.cancel()
            elif invoice.docstatus == 0:
                frappe.delete_doc("Sales Invoice", invoice.name, ignore_permissions=True)
        else:
            for row in route.items:
                status = frappe.db.get_value("CC Allocation", row.allocation, "status")
                if status == "RESERVED":
                    release_allocation(
                        row.allocation,
                        reason=f"POS checkout {checkout.name} compensated: {reason.strip()}",
                    )
        if route.print_job:
            job_status = frappe.db.get_value("CC POS Print Job", route.print_job, "status")
            if job_status not in {"SUCCEEDED", "CANCELLED"}:
                frappe.db.set_value(
                    "CC POS Print Job",
                    route.print_job,
                    "status",
                    "CANCELLED",
                    update_modified=False,
                )
        frappe.db.set_value(
            "CC POS Route",
            route.name,
            {"status": "COMPENSATED", "last_error": reason.strip()},
            update_modified=False,
        )
    frappe.db.set_value(
        "CC POS Checkout",
        checkout.name,
        {"status": "COMPENSATED", "last_error": reason.strip()},
        update_modified=False,
    )
    checkout.reload()
    return checkout


def update_pos_payment_state(name: str, state: str) -> Any:
    """Snapshot external payment capture so compensation never guesses about money."""
    import frappe

    if state not in {"NONE", "CAPTURED", "UNKNOWN", "REFUNDED"}:
        raise POSCheckoutError(f"Unsupported POS payment state {state!r}")
    checkout = frappe.get_doc("CC POS Checkout", name)
    allowed = {
        "NONE": {"NONE", "CAPTURED", "UNKNOWN"},
        "CAPTURED": {"CAPTURED", "REFUNDED", "UNKNOWN"},
        "UNKNOWN": {"UNKNOWN", "CAPTURED", "REFUNDED"},
        "REFUNDED": {"REFUNDED"},
    }
    if state not in allowed[checkout.payment_state]:
        raise POSCheckoutError(
            f"POS payment state cannot move from {checkout.payment_state} to {state}"
        )
    frappe.db.set_value(
        "CC POS Checkout",
        checkout.name,
        "payment_state",
        state,
        update_modified=False,
    )
    checkout.reload()
    return checkout


def process_pending_print_jobs() -> int:
    """Dispatch persistent jobs through an optional public provider hook."""
    import frappe

    providers = frappe.get_hooks("cc_pos_print_provider") or []
    if not providers:
        return 0
    provider = frappe.get_attr(providers[0])
    names = frappe.get_all(
        "CC POS Print Job",
        filters={"status": ("in", ["PENDING", "FAILED"]), "attempts": ("<", 5)},
        pluck="name",
        order_by="creation asc",
        limit=20,
    )
    processed = 0
    for name in names:
        locked = frappe.db.sql(
            """
            select name
            from `tabCC POS Print Job`
            where name = %s and status in ('PENDING', 'FAILED') and attempts < 5
            for update
            """,
            (name,),
        )
        if not locked:
            continue
        frappe.db.set_value(
            "CC POS Print Job",
            name,
            "status",
            "PROCESSING",
            update_modified=False,
        )
        frappe.db.commit()
        job = frappe.get_doc("CC POS Print Job", name)
        try:
            result = provider(
                {
                    "job": job.name,
                    "route": job.route,
                    "sales_invoice": job.sales_invoice,
                    "print_kind": job.print_kind,
                    "idempotency_key": job.idempotency_key,
                }
            ) or {}
            if not isinstance(result, dict):
                raise POSCheckoutError("POS print provider must return a mapping")
            mark_print_job_succeeded(
                job.name,
                provider_reference=str(result.get("provider_reference") or ""),
            )
        except Exception as exc:
            mark_print_job_failed(job.name, error=str(exc))
        frappe.db.commit()
        processed += 1
    return processed
