"""Gate 0g — prove one allocator can serve both stock domains.

The base spec assumes GSF needs its own allocator, because its scope is
`group + physical_location + item` instead of `item + company + cc_location`.
This spike tests that assumption against the code that already ships:
`allocate_global_fifo` accepts no company at all, so the whole scope difference
lives in the adapter that produces candidates, not in the allocation rule.

Frappe-independent on purpose — it runs in the static CI job, not on a site.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from erpnext_ua.consignment_and_commission.services.allocation import StockCandidate
from erpnext_ua.consignment_and_commission.services.candidates import (
    CandidateAdapter,
    CandidateQuery,
    preview_from_adapters,
)

# GSF layers are always owned stock of one Company, but the shared allocator
# still validates `source_method` against the commission map, so the spike
# borrows BUYOUT/OWN. Production needs one additive entry there
# (`GSF_LAYER` -> `OWN`); nothing else in the allocator has to change.
SPIKE_SOURCE_METHOD = "BUYOUT"
SPIKE_RELATIONSHIP_MODEL = "OWN"


@dataclass(frozen=True, slots=True)
class GSFLayerSnapshot:
    """One immutable cost layer of the shared physical pool."""

    layer_name: str
    item_code: str
    owner_company: str
    warehouse: str
    physical_location: str
    fifo_datetime: datetime
    receipt_name: str
    receipt_row_index: int
    active_balance: Decimal
    unit_cost: Decimal
    reserved_qty: Decimal = Decimal("0")
    layer_status: str = "OPEN"


@dataclass(frozen=True, slots=True)
class PlannedLine:
    """One allocator slice enriched with the ownership decision GSF needs."""

    sequence: int
    layer_name: str
    owner_company: str
    qty: Decimal
    cost: Decimal
    needs_reallocation: bool


class GroupStockPool:
    """Shared physical pool of several FOP companies at one location."""

    def __init__(self, layers: Iterable[GSFLayerSnapshot]) -> None:
        self._layers = {layer.layer_name: layer for layer in layers}

    @property
    def adapter(self) -> CandidateAdapter:
        return _GSFCandidateAdapter(self._layers.values())

    @property
    def warehouses(self) -> frozenset[str]:
        return frozenset(layer.warehouse for layer in self._layers.values())

    def plan(
        self,
        *,
        query: CandidateQuery,
        qty: Decimal,
        extra_adapters: Sequence[CandidateAdapter] = (),
    ) -> list[PlannedLine]:
        """Run the shared allocator and resolve ownership per selected slice.

        `query.company` is the selling FOP, not a stock filter: a slice owned by
        anyone else is what triggers MANAGEMENT_REALLOCATION.
        """
        slices = preview_from_adapters([self.adapter, *extra_adapters], query=query, qty=qty)
        lines = []
        for allocation in slices:
            layer = self._layers[allocation.lot_name]
            lines.append(
                PlannedLine(
                    sequence=allocation.sequence,
                    layer_name=layer.layer_name,
                    owner_company=layer.owner_company,
                    qty=allocation.qty,
                    cost=allocation.qty * layer.unit_cost,
                    needs_reallocation=layer.owner_company != query.company,
                )
            )
        return lines


def total_cost(lines: Sequence[PlannedLine]) -> Decimal:
    return sum((line.cost for line in lines), Decimal("0"))


def three_company_pool() -> GroupStockPool:
    """Fixture for §37.1: one item, one location, three FOP owners."""
    return GroupStockPool(
        [
            _layer("GSF-A", "FOP A", hour=8, qty="2", unit_cost="1000"),
            _layer("GSF-B", "FOP B", hour=9, qty="3", unit_cost="1100"),
            _layer("GSF-C", "FOP C", hour=10, qty="1", unit_cost="1200"),
        ]
    )


def pool_query(seller: str, pool: GroupStockPool) -> CandidateQuery:
    return CandidateQuery(
        item_code="ITEM-1",
        company=seller,
        location="Rivne Central",
        allowed_warehouses=pool.warehouses,
    )


class _GSFCandidateAdapter:
    """Candidates scoped by pool and location — deliberately not by Company."""

    def __init__(self, layers: Iterable[GSFLayerSnapshot]) -> None:
        self._layers = tuple(layers)

    def load(self, query: CandidateQuery) -> list[StockCandidate]:
        return [self._candidate(layer) for layer in self._layers if self._matches(layer, query)]

    def _matches(self, layer: GSFLayerSnapshot, query: CandidateQuery) -> bool:
        return (
            layer.item_code == query.item_code
            and layer.physical_location == query.location
            and layer.warehouse in query.allowed_warehouses
        )

    def _candidate(self, layer: GSFLayerSnapshot) -> StockCandidate:
        return StockCandidate(
            lot_name=layer.layer_name,
            item_code=layer.item_code,
            warehouse=layer.warehouse,
            location=layer.physical_location,
            source_method=SPIKE_SOURCE_METHOD,
            relationship_model=SPIKE_RELATIONSHIP_MODEL,
            fifo_datetime=layer.fifo_datetime,
            receipt_name=layer.receipt_name,
            receipt_row_index=layer.receipt_row_index,
            available_qty=layer.active_balance,
            reserved_qty=layer.reserved_qty,
            blocked=layer.layer_status == "BLOCKED",
        )


def _layer(
    name: str,
    owner: str,
    *,
    hour: int,
    qty: str,
    unit_cost: str,
    receipt: str | None = None,
    row_index: int = 1,
) -> GSFLayerSnapshot:
    return GSFLayerSnapshot(
        layer_name=name,
        item_code="ITEM-1",
        owner_company=owner,
        warehouse=f"Group Pool {owner} - GSF",
        physical_location="Rivne Central",
        fifo_datetime=datetime(2026, 7, 27, hour, 0),
        receipt_name=receipt or f"GSF-RECEIPT-{hour}",
        receipt_row_index=row_index,
        active_balance=Decimal(qty),
        unit_cost=Decimal(unit_cost),
    )
