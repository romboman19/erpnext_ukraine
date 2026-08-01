"""Public stock-domain provider for global physical FIFO planning."""

from __future__ import annotations

from dataclasses import replace

from erpnext_ua.group_stock_fifo.services.stock_domain import (
    DomainCandidate,
    StockDomainQuery,
)

from .integrations.candidates import CCStockLotCandidateAdapter
from .services.candidates import CandidateQuery


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
