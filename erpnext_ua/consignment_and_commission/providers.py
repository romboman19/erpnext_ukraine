"""Public stock-domain provider for global physical FIFO planning."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from erpnext_ua.group_stock_fifo.services.stock_domain import (
    DomainCandidate,
    PlannedDomainSlice,
    StockDomainQuery,
)

from .integrations.candidates import CCStockLotCandidateAdapter
from .integrations.reservations import (
    consume_allocation,
    release_allocation,
    reserve_planned_stock,
)
from .services.candidates import CandidateQuery
from .services.reservation import ReservationRequest


class CCStockDomainProvider:
    provider_id = "CC"

    def list_candidates(self, query: StockDomainQuery) -> list[DomainCandidate]:
        import frappe

        if not frappe.db.get_single_value("CC Settings", "enabled"):
            return []
        locations = frappe.get_all(
            "CC Location",
            filters={
                "gsf_physical_location": query.physical_location,
                "read_stock_enabled": 1,
                "disabled": 0,
            },
            fields=[
                "name",
                "company",
                "legal_entity_type",
                "legal_entity_name",
                "own_warehouse",
                "commission_warehouse",
                "consignment_warehouse",
            ],
            order_by="company asc, name asc",
        )
        result: list[DomainCandidate] = []
        adapter = CCStockLotCandidateAdapter()
        for location in locations:
            warehouses = frozenset(
                {
                    location.own_warehouse,
                    location.commission_warehouse,
                    location.consignment_warehouse,
                }
            )
            candidates = adapter.load(
                CandidateQuery(
                    item_code=query.item_code,
                    company=location.company,
                    location=location.name,
                    allowed_warehouses=warehouses,
                    serial_no=query.serial_no,
                    batch_no=query.batch_no,
                )
            )
            result.extend(
                DomainCandidate(
                    provider_id=self.provider_id,
                    candidate=replace(candidate, location=query.physical_location),
                    seller_company=location.company,
                    provider_location=location.name,
                    legal_entity_type=location.legal_entity_type,
                    legal_entity_name=location.legal_entity_name,
                    fiscal_policy=candidate.fiscal_policy,
                )
                for candidate in candidates
            )
        return result

    def reserve_planned(
        self,
        *,
        idempotency_key: str,
        item_code: str,
        slices: list[PlannedDomainSlice],
        serial_no: str | None,
        batch_no: str | None,
    ):
        """Reserve one exact CC route selected by the shared FIFO planner."""
        if not slices:
            raise ValueError("CC provider reservation requires planned slices")
        first = slices[0]
        route = (
            first.seller_company,
            first.provider_location,
            first.legal_entity_type,
            first.legal_entity_name,
            first.fiscal_route,
        )
        if any(
            (
                row.seller_company,
                row.provider_location,
                row.legal_entity_type,
                row.legal_entity_name,
                row.fiscal_route,
            )
            != route
            for row in slices
        ):
            raise ValueError("One CC reservation cannot mix legal or fiscal routes")
        allocations = [row.allocation for row in slices]
        return reserve_planned_stock(
            ReservationRequest(
                idempotency_key=idempotency_key,
                item_code=item_code,
                company=first.seller_company,
                location=first.provider_location,
                qty=sum((row.qty for row in allocations), Decimal("0")),
                allowed_warehouses=frozenset(row.warehouse for row in allocations),
                serial_no=serial_no,
                batch_no=batch_no,
            ),
            allocations,
        )

    def release(self, allocation_name: str, *, reason: str):
        return release_allocation(allocation_name, reason=reason)

    def consume(self, allocation_name: str, *, doctype: str, document: str):
        return consume_allocation(
            allocation_name,
            consumer_doctype=doctype,
            consumer_document=document,
        )
