"""Domain-neutral candidate planning for one physical FIFO scope."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from erpnext_ua.consignment_and_commission.services.allocation import (
    AllocationError,
    AllocationSlice,
    StockCandidate,
    allocate_global_fifo,
)


@dataclass(frozen=True, slots=True)
class StockDomainQuery:
    company_group: str
    physical_location: str
    seller_company: str
    item_code: str
    qty: Decimal
    serial_no: str | None = None
    batch_no: str | None = None
    fiscal_checkout: bool = True


@dataclass(frozen=True, slots=True)
class DomainCandidate:
    provider_id: str
    candidate: StockCandidate
    seller_company: str
    provider_location: str
    legal_entity_type: str
    legal_entity_name: str
    fiscal_policy: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedDomainSlice:
    provider_id: str
    seller_company: str
    provider_location: str
    legal_entity_type: str
    legal_entity_name: str
    fiscal_route: str
    allocation: AllocationSlice


class StockDomainProvider(Protocol):
    provider_id: str

    def list_candidates(self, query: StockDomainQuery) -> list[DomainCandidate]: ...


def plan_domain_fifo(
    query: StockDomainQuery,
    candidates: list[DomainCandidate],
) -> list[PlannedDomainSlice]:
    if not candidates:
        raise AllocationError(f"No stock-domain candidates exist for {query.item_code}")
    allowed = frozenset(entry.candidate.warehouse for entry in candidates)
    slices = allocate_global_fifo(
        [entry.candidate for entry in candidates],
        item_code=query.item_code,
        location=query.physical_location,
        qty=query.qty,
        allowed_warehouses=allowed,
        serial_no=query.serial_no,
        batch_no=query.batch_no,
    )
    indexed = _index_candidates(candidates)
    return [
        _planned_slice(query, row, indexed[_slice_key(row)])
        for row in slices
    ]


def _index_candidates(candidates: list[DomainCandidate]) -> dict[tuple, DomainCandidate]:
    indexed: dict[tuple, DomainCandidate] = {}
    for entry in candidates:
        key = _candidate_key(entry.candidate)
        if key in indexed:
            raise AllocationError(
                f"Stock identity {entry.candidate.lot_name} is exposed by multiple providers"
            )
        indexed[key] = entry
    return indexed


def _candidate_key(candidate: StockCandidate) -> tuple:
    return (
        candidate.lot_name,
        candidate.warehouse,
        candidate.serial_no,
        candidate.batch_no,
    )


def _slice_key(row: AllocationSlice) -> tuple:
    return row.lot_name, row.warehouse, row.serial_no, row.batch_no


def _planned_slice(
    query: StockDomainQuery,
    row: AllocationSlice,
    entry: DomainCandidate,
) -> PlannedDomainSlice:
    return PlannedDomainSlice(
        provider_id=entry.provider_id,
        seller_company=entry.seller_company,
        provider_location=entry.provider_location,
        legal_entity_type=entry.legal_entity_type,
        legal_entity_name=entry.legal_entity_name,
        fiscal_route=_fiscal_route(query, row, entry.fiscal_policy),
        allocation=row,
    )


def _fiscal_route(
    query: StockDomainQuery,
    row: AllocationSlice,
    fiscal_policy: str | None,
) -> str:
    if not query.fiscal_checkout:
        return "NON_FISCAL"
    if fiscal_policy == "FISCAL":
        return "FISCAL"
    if fiscal_policy == "NON_FISCAL":
        return "NON_FISCAL"
    return "NON_FISCAL" if row.relationship_model == "COMMISSION" else "FISCAL"
