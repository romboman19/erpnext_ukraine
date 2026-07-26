from datetime import datetime, timedelta
from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.allocation import (
    AllocationError,
    InsufficientStockError,
    StockCandidate,
    allocate_global_fifo,
)


class AllocationServiceTests(TestCase):
    def setUp(self) -> None:
        self.base_time = datetime(2026, 7, 13, 8, 0)
        self.warehouses = frozenset({"Own - PTU", "Commission - PTU", "Consignment - PTU"})

    def candidate(
        self,
        lot: str,
        warehouse: str,
        model: str,
        hour: int,
        qty: str = "2",
        source_method: str | None = None,
        **overrides: object,
    ) -> StockCandidate:
        source_method = source_method or {
            "OWN": "BUYOUT",
            "COMMISSION": "COMMISSION",
            "CONSIGNMENT": "CONSIGNMENT",
        }[model]
        values = {
            "lot_name": lot,
            "item_code": "ITEM-1",
            "warehouse": warehouse,
            "location": "Rivne",
            "source_method": source_method,
            "relationship_model": model,
            "fifo_datetime": self.base_time + timedelta(hours=hour),
            "receipt_name": f"RECEIPT-{hour}",
            "receipt_row_index": 1,
            "available_qty": Decimal(qty),
        }
        values.update(overrides)
        return StockCandidate(**values)  # type: ignore[arg-type]

    def test_global_fifo_crosses_all_technical_warehouses(self) -> None:
        candidates = [
            self.candidate("OWN", "Own - PTU", "OWN", 1),
            self.candidate("COM", "Commission - PTU", "COMMISSION", 0),
            self.candidate("CON", "Consignment - PTU", "CONSIGNMENT", 2),
        ]

        allocations = allocate_global_fifo(
            candidates,
            item_code="ITEM-1",
            location="Rivne",
            qty=Decimal("5"),
            allowed_warehouses=self.warehouses,
        )

        self.assertEqual([row.lot_name for row in allocations], ["COM", "OWN", "CON"])
        self.assertEqual([row.qty for row in allocations], [Decimal("2"), Decimal("2"), Decimal("1")])

    def test_four_source_methods_share_one_fifo_without_payment_priority(self) -> None:
        candidates = [
            self.candidate(
                "DEFERRED",
                "Own - PTU",
                "OWN",
                1,
                source_method="DEFERRED_PURCHASE",
            ),
            self.candidate("CON", "Consignment - PTU", "CONSIGNMENT", 3),
            self.candidate("BUYOUT", "Own - PTU", "OWN", 2, source_method="BUYOUT"),
            self.candidate("COM", "Commission - PTU", "COMMISSION", 0),
        ]

        allocations = allocate_global_fifo(
            candidates,
            item_code="ITEM-1",
            location="Rivne",
            qty=Decimal("7"),
            allowed_warehouses=self.warehouses,
        )

        self.assertEqual(
            [row.source_method for row in allocations],
            ["COMMISSION", "DEFERRED_PURCHASE", "BUYOUT", "CONSIGNMENT"],
        )
        self.assertEqual(
            [row.relationship_model for row in allocations],
            ["COMMISSION", "OWN", "OWN", "CONSIGNMENT"],
        )
        self.assertEqual(
            [row.qty for row in allocations],
            [Decimal("2"), Decimal("2"), Decimal("2"), Decimal("1")],
        )

    def test_source_method_must_match_relationship_model(self) -> None:
        with self.assertRaisesRegex(AllocationError, "requires relationship model OWN"):
            self.candidate(
                "INVALID",
                "Commission - PTU",
                "COMMISSION",
                0,
                source_method="DEFERRED_PURCHASE",
            )
        with self.assertRaisesRegex(AllocationError, "Unsupported stock source method"):
            self.candidate(
                "INVALID",
                "Own - PTU",
                "OWN",
                0,
                source_method="UNKNOWN",
            )

    def test_serial_has_priority_over_older_fifo_candidate(self) -> None:
        candidates = [
            self.candidate("OLD", "Own - PTU", "OWN", 0, serial_no="SER-OLD"),
            self.candidate("SCAN", "Consignment - PTU", "CONSIGNMENT", 3, qty="1", serial_no="SER-42"),
        ]

        allocations = allocate_global_fifo(
            candidates,
            item_code="ITEM-1",
            location="Rivne",
            qty=Decimal("1"),
            allowed_warehouses=self.warehouses,
            serial_no="SER-42",
        )

        self.assertEqual(allocations[0].lot_name, "SCAN")

    def test_batch_limits_candidates_before_fifo(self) -> None:
        candidates = [
            self.candidate("OLD", "Own - PTU", "OWN", 0, batch_no="BATCH-1"),
            self.candidate("MATCH", "Commission - PTU", "COMMISSION", 2, batch_no="BATCH-2"),
        ]

        allocations = allocate_global_fifo(
            candidates,
            item_code="ITEM-1",
            location="Rivne",
            qty=Decimal("1"),
            allowed_warehouses=self.warehouses,
            batch_no="BATCH-2",
        )

        self.assertEqual(allocations[0].lot_name, "MATCH")

    def test_reserved_quantity_is_not_allocatable(self) -> None:
        candidates = [
            self.candidate("RESERVED", "Commission - PTU", "COMMISSION", 0, reserved_qty=Decimal("2")),
            self.candidate("NEXT", "Own - PTU", "OWN", 1),
        ]

        allocations = allocate_global_fifo(
            candidates,
            item_code="ITEM-1",
            location="Rivne",
            qty=Decimal("1"),
            allowed_warehouses=self.warehouses,
        )

        self.assertEqual(allocations[0].lot_name, "NEXT")

    def test_fifo_tie_breaker_is_stable(self) -> None:
        timestamp = self.base_time
        candidates = [
            self.candidate(
                "LOT-B",
                "Own - PTU",
                "OWN",
                0,
                fifo_datetime=timestamp,
                receipt_name="RECEIPT-A",
                receipt_row_index=2,
            ),
            self.candidate(
                "LOT-A",
                "Commission - PTU",
                "COMMISSION",
                0,
                fifo_datetime=timestamp,
                receipt_name="RECEIPT-A",
                receipt_row_index=1,
            ),
        ]

        allocations = allocate_global_fifo(
            candidates,
            item_code="ITEM-1",
            location="Rivne",
            qty=Decimal("3"),
            allowed_warehouses=self.warehouses,
        )

        self.assertEqual([row.lot_name for row in allocations], ["LOT-A", "LOT-B"])

    def test_insufficient_stock_is_controlled(self) -> None:
        with self.assertRaises(InsufficientStockError):
            allocate_global_fifo(
                [self.candidate("ONLY", "Own - PTU", "OWN", 0, qty="1")],
                item_code="ITEM-1",
                location="Rivne",
                qty=Decimal("2"),
                allowed_warehouses=self.warehouses,
            )
