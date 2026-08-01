"""Frappe runtime for registered stock-domain candidate providers."""

from __future__ import annotations

from decimal import Decimal

import frappe

from erpnext_ua.consignment_and_commission.services.candidates import CandidateQuery

from .candidates import GSFLayerCandidateAdapter, source_warehouses
from .domain import GSFError
from .stock_domain import (
    DomainCandidate,
    PlannedDomainSlice,
    StockDomainQuery,
    plan_domain_fifo,
)

GSF_PROVIDER_ID = "GSF"


def plan_stock_domains(
    *,
    company_group: str,
    physical_location: str,
    seller_company: str,
    item_code: str,
    qty: Decimal,
    serial_no: str | None = None,
    batch_no: str | None = None,
    fiscal_checkout: bool = True,
    allowed_gsf_warehouses: frozenset[str] | None = None,
) -> list[PlannedDomainSlice]:
    query = StockDomainQuery(
        company_group=company_group,
        physical_location=physical_location,
        seller_company=seller_company,
        item_code=item_code,
        qty=qty,
        serial_no=serial_no,
        batch_no=batch_no,
        fiscal_checkout=fiscal_checkout,
    )
    candidates = _gsf_candidates(query, allowed_warehouses=allowed_gsf_warehouses)
    for provider_path in frappe.get_hooks("stock_domain_providers") or []:
        provider = frappe.get_attr(provider_path)()
        candidates.extend(provider.list_candidates(query))
    try:
        return plan_domain_fifo(query, candidates)
    except ValueError as error:
        raise GSFError(str(error), "INSUFFICIENT_GLOBAL_STOCK") from error


def _gsf_candidates(
    query: StockDomainQuery,
    *,
    allowed_warehouses: frozenset[str] | None,
) -> list[DomainCandidate]:
    pools = source_warehouses(
        company_group=query.company_group,
        physical_location=query.physical_location,
    )
    warehouse_scope = frozenset(pools)
    if allowed_warehouses is not None:
        warehouse_scope &= allowed_warehouses
    adapter = GSFLayerCandidateAdapter(
        company_group=query.company_group,
        physical_location=query.physical_location,
    )
    candidates = adapter.load(
        CandidateQuery(
            item_code=query.item_code,
            company=query.seller_company,
            location=query.physical_location,
            allowed_warehouses=warehouse_scope,
            serial_no=query.serial_no,
            batch_no=query.batch_no,
        )
    )
    return [
        DomainCandidate(
            provider_id=GSF_PROVIDER_ID,
            candidate=candidate,
            seller_company=query.seller_company,
            provider_location=query.physical_location,
            legal_entity_type="Company",
            legal_entity_name=query.seller_company,
            fiscal_policy="FISCAL",
        )
        for candidate in candidates
    ]


def require_gsf_only(slices: list[PlannedDomainSlice]) -> list:
    external = [row for row in slices if row.provider_id != GSF_PROVIDER_ID]
    if external:
        first = external[0]
        raise GSFError(
            f"Global FIFO selected {first.allocation.source_method} stock "
            f"{first.allocation.lot_name} of {first.seller_company}; POS-UA mixed stock routes "
            "are required before this basket can be posted",
            "MIXED_STOCK_ROUTE_REQUIRED",
        )
    return [row.allocation for row in slices]
