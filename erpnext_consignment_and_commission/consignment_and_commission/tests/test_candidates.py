from datetime import datetime
from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.allocation import (
    StockCandidate,
)
from erpnext_consignment_and_commission.consignment_and_commission.services.candidates import (
    CandidateAdapterError,
    CandidateQuery,
    CCStockLotSnapshot,
    candidates_from_cc_stock_lot,
    preview_from_adapters,
)


class StaticCandidateAdapter:
    def __init__(self, candidates: list[StockCandidate]) -> None:
        self.candidates = candidates

    def load(self, query: CandidateQuery) -> list[StockCandidate]:
        return [candidate for candidate in self.candidates if candidate.item_code == query.item_code]


class CandidateAdapterTests(TestCase):
    def snapshot(self, **overrides: object) -> CCStockLotSnapshot:
        values = {
            "lot_name": "LOT-COMMISSION",
            "item_code": "ITEM-1",
            "warehouse": "Commission - PTU",
            "location": "Rivne",
            "source_method": "COMMISSION",
            "relationship_model": "COMMISSION",
            "fifo_datetime": datetime(2026, 7, 13, 8, 0),
            "receipt_name": "RECEIPT-1",
            "receipt_row_index": 1,
            "received_qty": Decimal("5"),
            "active_balance": Decimal("3"),
            "reserved_qty": Decimal("1"),
            "lot_status": "OPEN",
            "tracking_type": "NONE",
        }
        values.update(overrides)
        return CCStockLotSnapshot(**values)  # type: ignore[arg-type]

    def test_untracked_snapshot_preserves_ledger_balance_and_fifo_coordinates(self) -> None:
        candidates = candidates_from_cc_stock_lot(self.snapshot())

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.source_method, "COMMISSION")
        self.assertEqual(candidate.available_qty, Decimal("3"))
        self.assertEqual(candidate.reserved_qty, Decimal("1"))
        self.assertEqual(candidate.allocatable_qty, Decimal("2"))
        self.assertEqual(candidate.status, "Partially Sold")
        self.assertEqual(candidate.receipt_row_index, 1)

    def test_serial_snapshot_becomes_one_candidate_per_active_identity(self) -> None:
        candidates = candidates_from_cc_stock_lot(
            self.snapshot(
                received_qty=Decimal("3"),
                active_balance=Decimal("2"),
                reserved_qty=Decimal("0"),
                tracking_type="SERIAL",
                serial_numbers=("SER-1", "SER-2", "SER-3"),
                available_serial_numbers=("SER-1", "SER-3"),
            )
        )

        self.assertEqual([candidate.serial_no for candidate in candidates], ["SER-1", "SER-3"])
        self.assertEqual([candidate.available_qty for candidate in candidates], [Decimal("1")] * 2)

    def test_serial_snapshot_reconciles_identity_level_reservations(self) -> None:
        candidates = candidates_from_cc_stock_lot(
            self.snapshot(
                received_qty=Decimal("2"),
                active_balance=Decimal("2"),
                reserved_qty=Decimal("1"),
                tracking_type="SERIAL",
                serial_numbers=("SER-1", "SER-2"),
                available_serial_numbers=("SER-1", "SER-2"),
                reserved_serial_numbers=("SER-1",),
            )
        )
        self.assertEqual([row.reserved_qty for row in candidates], [Decimal("1"), Decimal("0")])

        with self.assertRaisesRegex(CandidateAdapterError, "does not match identities"):
            candidates_from_cc_stock_lot(
                self.snapshot(
                    received_qty=Decimal("2"),
                    active_balance=Decimal("2"),
                    reserved_qty=Decimal("1"),
                    tracking_type="SERIAL",
                    serial_numbers=("SER-1", "SER-2"),
                    available_serial_numbers=("SER-1", "SER-2"),
                )
            )

    def test_preview_merges_adapters_before_global_fifo(self) -> None:
        commission = candidates_from_cc_stock_lot(
            self.snapshot(active_balance=Decimal("2"), reserved_qty=Decimal("0"))
        )[0]
        own = StockCandidate(
            lot_name="OWN-BUYOUT",
            item_code="ITEM-1",
            warehouse="Own - PTU",
            location="Rivne",
            source_method="BUYOUT",
            relationship_model="OWN",
            fifo_datetime=datetime(2026, 7, 13, 9, 0),
            receipt_name="PINV-1",
            receipt_row_index=1,
            available_qty=Decimal("2"),
        )
        query = CandidateQuery(
            item_code="ITEM-1",
            company="Test Company",
            location="Rivne",
            allowed_warehouses=frozenset({"Own - PTU", "Commission - PTU"}),
        )

        allocations = preview_from_adapters(
            [StaticCandidateAdapter([own]), StaticCandidateAdapter([commission])],
            query=query,
            qty=Decimal("3"),
        )

        self.assertEqual([row.lot_name for row in allocations], ["LOT-COMMISSION", "OWN-BUYOUT"])
        self.assertEqual([row.qty for row in allocations], [Decimal("2"), Decimal("1")])
