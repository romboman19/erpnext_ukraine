"""Frappe adapter for ledger-backed CC Stock Lot allocation candidates."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from ..services.allocation import StockCandidate
from ..services.candidates import (
    CandidateAdapterError,
    CandidateQuery,
    CCStockLotSnapshot,
    candidates_from_cc_stock_lot,
)
from ..setup.ownership_dimension import OWNERSHIP_FIELD


def _active_balances(
    frappe: Any,
    *,
    item_code: str,
    lot_names: list[str],
) -> dict[tuple[str, str], Decimal]:
    if not lot_names:
        return {}
    placeholders = ", ".join(["%s"] * len(lot_names))
    rows = frappe.db.sql(
        f"""
        select `{OWNERSHIP_FIELD}` as lot_name, warehouse,
               coalesce(sum(actual_qty), 0) as balance
        from `tabStock Ledger Entry`
        where item_code = %s
          and `{OWNERSHIP_FIELD}` in ({placeholders})
          and is_cancelled = 0
        group by `{OWNERSHIP_FIELD}`, warehouse
        """,
        (item_code, *lot_names),
        as_dict=True,
    )
    return {(row.lot_name, row.warehouse): Decimal(str(row.balance or 0)) for row in rows}


def _assert_no_unclassified_balance(
    frappe: Any,
    *,
    item_code: str,
    warehouses: frozenset[str],
) -> None:
    if not warehouses:
        return
    placeholders = ", ".join(["%s"] * len(warehouses))
    rows = frappe.db.sql(
        f"""
        select warehouse,
               coalesce(sum(case
                   when `{OWNERSHIP_FIELD}` is null or `{OWNERSHIP_FIELD}` = '' then actual_qty
                   else 0
               end), 0) as unclassified_balance
        from `tabStock Ledger Entry`
        where item_code = %s
          and warehouse in ({placeholders})
          and is_cancelled = 0
        group by warehouse
        """,
        (item_code, *sorted(warehouses)),
        as_dict=True,
    )
    unclassified = {
        row.warehouse: Decimal(str(row.unclassified_balance or 0))
        for row in rows
        if Decimal(str(row.unclassified_balance or 0)) != 0
    }
    if unclassified:
        details = ", ".join(
            f"{warehouse}={balance}" for warehouse, balance in sorted(unclassified.items())
        )
        raise CandidateAdapterError(
            f"Item {item_code} has unclassified stock in CC technical warehouses: {details}"
        )


def _reserved_serials(frappe: Any, lot_names: list[str]) -> dict[str, set[str]]:
    if not lot_names:
        return {}
    if not frappe.db.exists("DocType", "CC Allocation"):
        raise CandidateAdapterError("CC Allocation schema is not synchronized; run bench migrate")
    placeholders = ", ".join(["%s"] * len(lot_names))
    rows = frappe.db.sql(
        f"""
        select slice.stock_lot, slice.serial_no
        from `tabCC Allocation Slice` slice
        inner join `tabCC Allocation` allocation on allocation.name = slice.parent
        where slice.stock_lot in ({placeholders})
          and slice.serial_no is not null
          and slice.serial_no != ''
          and allocation.status = 'RESERVED'
        """,
        tuple(lot_names),
        as_dict=True,
    )
    values: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        values[row.stock_lot].add(row.serial_no)
    return values


class CCStockLotCandidateAdapter:
    """Load every classified CC Stock Lot without mutating ERPNext state."""

    def load(self, query: CandidateQuery) -> list[StockCandidate]:
        import frappe
        from frappe.utils import get_datetime

        if not query.allowed_warehouses:
            return []
        if not frappe.db.has_column("Stock Ledger Entry", OWNERSHIP_FIELD):
            raise CandidateAdapterError(
                "CC Stock Lot Inventory Dimension is not synchronized; run bench migrate"
            )
        _assert_no_unclassified_balance(
            frappe,
            item_code=query.item_code,
            warehouses=query.allowed_warehouses,
        )

        lots = frappe.get_all(
            "CC Stock Lot",
            filters={
                "company": query.company,
                "location": query.location,
                "item_code": query.item_code,
                "warehouse": ("in", sorted(query.allowed_warehouses)),
                "lot_status": ("in", ["OPEN", "BLOCKED"]),
            },
            fields=[
                "name",
                "receipt",
                "receipt_item_row",
                "contract",
                "own_receipt",
                "own_receipt_item_row",
                "source_method",
                "relationship_model",
                "item_code",
                "warehouse",
                "received_datetime",
                "received_qty",
                "reserved_qty",
                "lot_status",
                "tracking_type",
                "batch_no",
                "serial_numbers",
            ],
            order_by="received_datetime asc, source_method asc, name asc",
        )
        if not lots:
            return []

        lot_names = [lot.name for lot in lots]
        balances = _active_balances(frappe, item_code=query.item_code, lot_names=lot_names)
        receipt_rows = [lot.receipt_item_row for lot in lots if lot.receipt_item_row]
        row_indexes = (
            {
                row.name: int(row.idx or 0)
                for row in frappe.get_all(
                    "CC Receipt Item",
                    filters={"name": ("in", receipt_rows)},
                    fields=["name", "idx"],
                )
            }
            if receipt_rows
            else {}
        )
        own_receipt_rows = [lot.own_receipt_item_row for lot in lots if lot.own_receipt_item_row]
        own_row_indexes = (
            {
                row.name: int(row.idx or 0)
                for row in frappe.get_all(
                    "CC Own Receipt Item",
                    filters={"name": ("in", own_receipt_rows)},
                    fields=["name", "idx"],
                )
            }
            if own_receipt_rows
            else {}
        )
        contract_names = sorted({lot.contract for lot in lots if lot.contract})
        fiscal_policies = (
            {
                row.name: row.fiscal_policy
                for row in frappe.get_all(
                    "CC Contract",
                    filters={"name": ("in", contract_names)},
                    fields=["name", "fiscal_policy"],
                )
            }
            if contract_names
            else {}
        )

        serials_by_lot: dict[str, set[str]] = defaultdict(set)
        serial_lot_names = [lot.name for lot in lots if lot.tracking_type == "SERIAL"]
        reserved_serials_by_lot = _reserved_serials(frappe, serial_lot_names)
        lot_warehouses = {lot.name: lot.warehouse for lot in lots}
        if serial_lot_names:
            for serial in frappe.get_all(
                "Serial No",
                filters={OWNERSHIP_FIELD: ("in", serial_lot_names)},
                fields=["name", OWNERSHIP_FIELD, "warehouse"],
            ):
                lot_name = serial.get(OWNERSHIP_FIELD)
                if serial.warehouse == lot_warehouses.get(lot_name):
                    serials_by_lot[lot_name].add(serial.name)

        candidates: list[StockCandidate] = []
        for lot in lots:
            relationship_model = lot.relationship_model
            if relationship_model not in {"OWN", "COMMISSION", "CONSIGNMENT"}:
                raise CandidateAdapterError(
                    f"CC Stock Lot {lot.name} has unsupported relationship model {relationship_model}"
                )
            source_method = lot.source_method
            if source_method not in {
                "BUYOUT",
                "DEFERRED_PURCHASE",
                "COMMISSION",
                "CONSIGNMENT",
            }:
                raise CandidateAdapterError(
                    f"CC Stock Lot {lot.name} has unsupported source method {source_method}"
                )
            serial_numbers = tuple(value for value in (lot.serial_numbers or "").splitlines() if value)
            active_serials = serials_by_lot.get(lot.name, set())
            ordered_active_serials = tuple(value for value in serial_numbers if value in active_serials)
            snapshot = CCStockLotSnapshot(
                lot_name=lot.name,
                item_code=lot.item_code,
                warehouse=lot.warehouse,
                location=query.location,
                source_method=source_method,
                relationship_model=relationship_model,
                fifo_datetime=get_datetime(lot.received_datetime),
                receipt_name=lot.receipt or lot.own_receipt,
                receipt_row_index=(
                    row_indexes.get(lot.receipt_item_row, 0)
                    if lot.receipt_item_row
                    else own_row_indexes.get(lot.own_receipt_item_row, 0)
                ),
                received_qty=Decimal(str(lot.received_qty or 0)),
                active_balance=balances.get((lot.name, lot.warehouse), Decimal("0")),
                reserved_qty=Decimal(str(lot.reserved_qty or 0)),
                lot_status=lot.lot_status,
                tracking_type=lot.tracking_type,
                batch_no=lot.batch_no,
                serial_numbers=serial_numbers,
                available_serial_numbers=ordered_active_serials,
                reserved_serial_numbers=tuple(
                    value
                    for value in serial_numbers
                    if value in reserved_serials_by_lot.get(lot.name, set())
                ),
                fiscal_policy=fiscal_policies.get(lot.contract),
            )
            candidates.extend(candidates_from_cc_stock_lot(snapshot))
        return candidates
