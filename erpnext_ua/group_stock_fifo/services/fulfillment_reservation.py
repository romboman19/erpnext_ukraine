"""Atomic cross-domain reservation for one channel-neutral sale line."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import frappe

from .allocations import lock_scope, release_allocation, reserve_planned
from .candidates import source_warehouses
from .domain import GSFError
from .fulfillment_domain import (
    FulfillmentRouteKey,
    ProviderAllocationRef,
    route_key,
    split_discount,
)
from .reservation import ReservationRequest
from .stock_domain import PlannedDomainSlice
from .stock_domain_runtime import GSF_PROVIDER_ID, plan_stock_domains


def reserve_checkout_line(checkout: Any, line: Any) -> list[ProviderAllocationRef]:
    """Plan global FIFO once, then reserve every provider in one transaction."""
    request = _gsf_request(
        checkout,
        line,
        qty=Decimal(str(line.qty)),
        allowed_warehouses=frozenset(
            source_warehouses(
                company_group=checkout.company_group,
                physical_location=checkout.physical_location,
            )
        ),
        suffix="scope",
    )
    lock_scope(request)
    planned = plan_stock_domains(
        company_group=checkout.company_group,
        physical_location=checkout.physical_location,
        seller_company=checkout.seller_company,
        item_code=line.item_code,
        qty=Decimal(str(line.qty)),
        serial_no=line.serial_no,
        batch_no=line.batch_no,
        fiscal_checkout=checkout.fiscal_state == "PENDING",
    )
    grouped = _group_routes(planned)
    discounts = split_discount(
        Decimal(str(line.discount_amount or 0)),
        [sum((row.allocation.qty for row in rows), Decimal("0")) for _route, rows in grouped],
    )
    providers = _providers()
    result: list[ProviderAllocationRef] = []
    for group_index, ((route, rows), discount) in enumerate(zip(grouped, discounts, strict=True), 1):
        allocation = _reserve_route(
            checkout,
            line,
            route=route,
            rows=rows,
            provider=providers.get(route.provider_id),
            reservation_suffix=f"{route.stable_id}:{group_index}",
        )
        result.append(
            ProviderAllocationRef(
                route=route,
                allocation_doctype=(
                    "GSF Allocation" if route.provider_id == GSF_PROVIDER_ID else "CC Allocation"
                ),
                allocation_name=allocation.name,
                item_code=line.item_code,
                qty=sum((row.allocation.qty for row in rows), Decimal("0")),
                external_row_id=line.external_row_id,
                rate=Decimal(str(line.rate)),
                discount_amount=discount,
            )
        )
    return result


def serialize_refs(refs: list[ProviderAllocationRef]) -> str:
    return json.dumps([row.as_dict() for row in refs], sort_keys=True, separators=(",", ":"))


def deserialize_refs(value: str | None) -> list[ProviderAllocationRef]:
    result = []
    for row in json.loads(value or "[]"):
        result.append(
            ProviderAllocationRef(
                route=FulfillmentRouteKey(**row["route"]),
                allocation_doctype=row["allocation_doctype"],
                allocation_name=row["allocation_name"],
                item_code=row["item_code"],
                qty=Decimal(row["qty"]),
                external_row_id=row.get("external_row_id"),
                rate=Decimal(row["rate"]),
                discount_amount=Decimal(row.get("discount_amount") or "0"),
            )
        )
    return result


def checkout_refs(checkout: Any) -> list[ProviderAllocationRef]:
    return [
        ref
        for line in checkout.lines
        for ref in deserialize_refs(line.get("route_allocations"))
    ]


def release_ref(ref: ProviderAllocationRef, *, reason: str) -> None:
    if ref.route.provider_id == GSF_PROVIDER_ID:
        release_allocation(ref.allocation_name, reason=reason)
        return
    provider = _providers().get(ref.route.provider_id)
    if not provider:
        raise GSFError(
            f"Stock provider {ref.route.provider_id} is unavailable",
            "MANUAL_REVIEW_REQUIRED",
        )
    provider.release(ref.allocation_name, reason=reason)


def _group_routes(
    planned: list[PlannedDomainSlice],
) -> list[tuple[FulfillmentRouteKey, list[PlannedDomainSlice]]]:
    grouped: list[tuple[FulfillmentRouteKey, list[PlannedDomainSlice]]] = []
    for row in planned:
        route = route_key(row)
        if grouped and grouped[-1][0] == route:
            grouped[-1][1].append(row)
        else:
            grouped.append((route, [row]))
    return grouped


def _reserve_route(
    checkout: Any,
    line: Any,
    *,
    route: FulfillmentRouteKey,
    rows: list[PlannedDomainSlice],
    provider: Any | None,
    reservation_suffix: str,
) -> Any:
    suffix = reservation_suffix
    if route.provider_id == GSF_PROVIDER_ID:
        slices = [row.allocation for row in rows]
        return reserve_planned(
            _gsf_request(
                checkout,
                line,
                qty=sum((row.qty for row in slices), Decimal("0")),
                allowed_warehouses=frozenset(row.warehouse for row in slices),
                suffix=suffix,
            ),
            slices,
            scope_locked=True,
        )
    if not provider or not hasattr(provider, "reserve_planned"):
        raise GSFError(
            f"Stock provider {route.provider_id} cannot reserve its FIFO slices",
            "MIXED_STOCK_ROUTE_REQUIRED",
        )
    return provider.reserve_planned(
        idempotency_key=f"{checkout.idempotency_key}:{line.idx}:{suffix}",
        item_code=line.item_code,
        slices=rows,
        serial_no=line.serial_no,
        batch_no=line.batch_no,
    )


def _gsf_request(
    checkout: Any,
    line: Any,
    *,
    qty: Decimal,
    allowed_warehouses: frozenset[str],
    suffix: str,
) -> ReservationRequest:
    return ReservationRequest(
        idempotency_key=f"{checkout.idempotency_key}:{line.idx}:{suffix}",
        company_group=checkout.company_group,
        physical_location=checkout.physical_location,
        seller_company=checkout.seller_company,
        item_code=line.item_code,
        qty=qty,
        allowed_warehouses=allowed_warehouses,
        serial_no=line.serial_no,
        batch_no=line.batch_no,
        external_row_id=line.external_row_id,
        checkout=checkout.name,
    )


def _providers() -> dict[str, Any]:
    return {
        provider.provider_id: provider
        for path in frappe.get_hooks("stock_domain_providers") or []
        for provider in (frappe.get_attr(path)(),)
    }
