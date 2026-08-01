"""§12.2 eligible candidates, fed to the shared allocator (ADR-013).

Gate 0g found `allocate_global_fifo` already company-agnostic, so GSF does not
get a second allocator — it gets a second adapter. Everything specific to the
group-FIFO domain lives here: which warehouses are in scope, and how much of a
layer is really available.

Available quantity is read from the **Stock Ledger Entry aggregate**, never from
`GSF Layer Balance.actual_qty_cache`. §9.10 allows that cache to lag, and
allocating against a lagging number is exactly how stock gets sold twice.
Reserved quantity is the opposite case: reservations have no ledger
representation at all, so the balance row owns that number and the write path
guards it with a conditional update.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import frappe

from erpnext_ua.consignment_and_commission.services.allocation import StockCandidate
from erpnext_ua.consignment_and_commission.services.candidates import CandidateQuery

from .domain import OWN_POOL_ROLE, TRACKING_SERIAL, balance_identity
from .reservation import (
    GSF_RELATIONSHIP_MODEL,
    GSF_SOURCE_METHOD,
    LIVE_ALLOCATION_STATUSES,
)
from .serial_identity import ordered_active_serials

#: Statuses whose stock is still part of the pool. `BLOCKED` layers are loaded
#: and flagged rather than filtered out, so a "why was this not allocated"
#: question has an answer instead of a silent absence.
CANDIDATE_LAYER_STATUSES = ("OPEN", "BLOCKED")


def source_warehouses(*, company_group: str, physical_location: str) -> dict[str, str]:
    """§12.2: OWN Pools of active sourcing members at one location, warehouse → company."""
    rows = frappe.db.sql(
        """
        select binding.warehouse, binding.company
        from `tabGSF Warehouse Binding` binding
        join `tabGSF Group Member` member
          on member.company = binding.company
         and member.parent = %(group)s
         and member.enabled = 1
         and member.can_source_stock = 1
        where binding.enabled = 1
          and binding.manager_app = 'GSF'
          and binding.warehouse_role = %(role)s
          and binding.company_group = %(group)s
          and binding.physical_location = %(location)s
        """,
        {"group": company_group, "location": physical_location, "role": OWN_POOL_ROLE},
        as_dict=True,
    )
    return {row.warehouse: row.company for row in rows}


@dataclass(frozen=True, slots=True)
class LayerPosition:
    """One layer's stock in one company's pool, as the ledger currently reports it."""

    stock_layer: str
    company: str
    warehouse: str
    actual_qty: Decimal
    reserved_qty: Decimal
    item_code: str
    physical_location: str
    layer_status: str
    tracking_type: str
    batch_no: str | None
    serial_numbers: tuple[str, ...]
    fifo_datetime: object
    origin_doctype: str
    origin_document: str
    origin_row_index: int

    @property
    def available_qty(self) -> Decimal:
        return max(self.actual_qty - self.reserved_qty, Decimal("0"))


class GSFLayerCandidateAdapter:
    """Candidates scoped by group and location — deliberately never by seller.

    §12.6: the seller arrives in the request; FIFO decides only which stock and
    whose it is. Filtering candidates by the seller here is the single mistake
    that would turn global FIFO back into seller-first.
    """

    def __init__(self, *, company_group: str, physical_location: str) -> None:
        self.company_group = company_group
        self.physical_location = physical_location

    def load(self, query: CandidateQuery) -> list[StockCandidate]:
        positions = self.positions(query)
        if any(position.tracking_type == TRACKING_SERIAL for position in positions):
            if not query.serial_no:
                from .domain import GSFError

                raise GSFError(
                    f"Serial No is required to allocate {query.item_code}",
                    "SERIAL_AMBIGUOUS",
                )
            reserved = _reserved_serials(query.serial_no)
        else:
            reserved = set()
        return [
            candidate
            for position in positions
            for candidate in _candidates_from(position, reserved_serials=reserved)
        ]

    def positions(self, query: CandidateQuery) -> list[LayerPosition]:
        """Every eligible layer position, already in §12.3 order.

        The order matters beyond tidiness. The shared allocator re-sorts by
        (fifo datetime, origin document, origin row, layer), which is a prefix
        of §12.3's key; Python's sort is stable, so returning rows pre-sorted by
        the full key is what supplies the trailing company/warehouse tie-break
        for a layer that sits in more than one pool.
        """
        pools = source_warehouses(
            company_group=self.company_group, physical_location=self.physical_location
        )
        allowed = {
            warehouse: company
            for warehouse, company in pools.items()
            if warehouse in query.allowed_warehouses
        }
        if not allowed:
            return []

        ledger = _ledger_positions(item_code=query.item_code, warehouses=tuple(allowed))
        if not ledger:
            return []

        layers = _layer_metadata(
            names=tuple({stock_layer for stock_layer, _ in ledger}),
            company_group=self.company_group,
            physical_location=self.physical_location,
            item_code=query.item_code,
        )

        positions = []
        for (stock_layer, warehouse), actual_qty in ledger.items():
            layer = layers.get(stock_layer)
            if not layer:
                continue
            company = allowed[warehouse]
            positions.append(
                LayerPosition(
                    stock_layer=stock_layer,
                    company=company,
                    warehouse=warehouse,
                    actual_qty=actual_qty,
                    reserved_qty=_reserved_qty(stock_layer, company, warehouse),
                    item_code=layer.item_code,
                    physical_location=layer.physical_location,
                    layer_status=layer.layer_status,
                    tracking_type=layer.tracking_type,
                    batch_no=layer.batch_no or None,
                    serial_numbers=tuple(
                        line.strip()
                        for line in (layer.serial_numbers or "").splitlines()
                        if line.strip()
                    ),
                    fifo_datetime=layer.original_received_datetime,
                    origin_doctype=layer.origin_doctype,
                    origin_document=layer.origin_document,
                    origin_row_index=layer.origin_row_index or 0,
                )
            )
        return sorted(positions, key=_fifo_key)


def _fifo_key(position: LayerPosition):
    """§12.3, in full. Company and warehouse are tie-breakers, never priorities."""
    return (
        position.fifo_datetime,
        position.origin_doctype,
        position.origin_document,
        position.origin_row_index,
        position.stock_layer,
        position.company,
        position.warehouse,
    )


def _ledger_positions(*, item_code: str, warehouses: tuple[str, ...]) -> dict[tuple[str, str], Decimal]:
    rows = frappe.db.sql(
        """
        select sle.gsf_stock_layer as stock_layer, sle.warehouse, sum(sle.actual_qty) as qty
        from `tabStock Ledger Entry` sle
        where sle.item_code = %(item_code)s
          and sle.is_cancelled = 0
          and sle.gsf_stock_layer is not null
          and sle.gsf_stock_layer != ''
          and sle.warehouse in %(warehouses)s
        group by sle.gsf_stock_layer, sle.warehouse
        having sum(sle.actual_qty) > 0
        """,
        {"item_code": item_code, "warehouses": warehouses},
        as_dict=True,
    )
    return {(row.stock_layer, row.warehouse): Decimal(str(row.qty)) for row in rows}


def _layer_metadata(
    *, names: tuple[str, ...], company_group: str, physical_location: str, item_code: str
) -> dict[str, object]:
    rows = frappe.get_all(
        "GSF Stock Layer",
        filters={
            "name": ("in", names),
            "company_group": company_group,
            "physical_location": physical_location,
            "item_code": item_code,
            "layer_status": ("in", CANDIDATE_LAYER_STATUSES),
        },
        fields=[
            "name",
            "item_code",
            "physical_location",
            "layer_status",
            "tracking_type",
            "batch_no",
            "serial_numbers",
            "original_received_datetime",
            "origin_doctype",
            "origin_document",
            "origin_row_index",
        ],
    )
    return {row.name: row for row in rows}


def _reserved_qty(stock_layer: str, company: str, warehouse: str) -> Decimal:
    reserved = frappe.db.get_value(
        "GSF Layer Balance",
        balance_identity(stock_layer=stock_layer, company=company, warehouse=warehouse),
        "reserved_qty_cache",
    )
    return Decimal(str(reserved or 0))


def _reserved_serials(serial_no: str) -> set[str]:
    rows = frappe.db.sql(
        """
        select distinct slice.serial_no
        from `tabGSF Allocation Slice` slice
        inner join `tabGSF Allocation` allocation on allocation.name = slice.parent
        where slice.serial_no = %(serial_no)s
          and allocation.status in %(statuses)s
        """,
        {"serial_no": serial_no, "statuses": LIVE_ALLOCATION_STATUSES},
        as_dict=True,
    )
    return {row.serial_no for row in rows}


def _available_serials(position: LayerPosition) -> tuple[str, ...]:
    if not position.serial_numbers:
        from .domain import GSFError

        raise GSFError(
            f"Serial-tracked layer {position.stock_layer} has no Serial Nos",
            "SERIAL_AMBIGUOUS",
        )
    rows = frappe.get_all(
        "Serial No",
        filters={
            "name": ("in", position.serial_numbers),
            "item_code": position.item_code,
            "warehouse": position.warehouse,
        },
        pluck="name",
    )
    return ordered_active_serials(
        position.serial_numbers,
        set(rows),
        actual_qty=position.actual_qty,
        context=f"Layer {position.stock_layer} in {position.warehouse}",
    )


def _candidates_from(
    position: LayerPosition,
    *,
    reserved_serials: set[str] | None = None,
) -> list[StockCandidate]:
    if position.tracking_type == TRACKING_SERIAL:
        reserved = reserved_serials or set()
        return [
            StockCandidate(
                lot_name=position.stock_layer,
                item_code=position.item_code,
                warehouse=position.warehouse,
                location=position.physical_location,
                source_method=GSF_SOURCE_METHOD,
                relationship_model=GSF_RELATIONSHIP_MODEL,
                fifo_datetime=position.fifo_datetime,
                receipt_name=position.origin_document,
                receipt_row_index=max(position.origin_row_index, 1),
                blocked=position.layer_status == "BLOCKED",
                available_qty=Decimal("1"),
                reserved_qty=Decimal("1") if serial_no in reserved else Decimal("0"),
                serial_no=serial_no,
            )
            for serial_no in _available_serials(position)
        ]

    return [
        StockCandidate(
            lot_name=position.stock_layer,
            item_code=position.item_code,
            warehouse=position.warehouse,
            location=position.physical_location,
            source_method=GSF_SOURCE_METHOD,
            relationship_model=GSF_RELATIONSHIP_MODEL,
            fifo_datetime=position.fifo_datetime,
            # The allocator's own tie-breakers, filled from §12.3's coordinates.
            receipt_name=position.origin_document,
            receipt_row_index=max(position.origin_row_index, 1),
            blocked=position.layer_status == "BLOCKED",
            available_qty=position.actual_qty,
            reserved_qty=position.reserved_qty,
            batch_no=position.batch_no,
        )
    ]


def preview(
    *, company_group: str, physical_location: str, query: CandidateQuery, qty: Decimal
) -> list:
    """Read-only §12.4 plan. Takes no locks and writes nothing."""
    from erpnext_ua.consignment_and_commission.services.candidates import preview_from_adapters

    adapter = GSFLayerCandidateAdapter(
        company_group=company_group, physical_location=physical_location
    )
    return preview_from_adapters([adapter], query=query, qty=qty)
