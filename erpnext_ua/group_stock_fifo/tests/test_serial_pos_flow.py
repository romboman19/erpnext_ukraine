"""Pure contracts for exact Serial identity from POS to return."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.allocation import (
    StockCandidate,
    allocate_global_fifo,
)
from erpnext_ua.group_stock_fifo.services.domain import GSFError
from erpnext_ua.group_stock_fifo.services.pos_return_domain import consume_return_rows
from erpnext_ua.group_stock_fifo.services.serial_identity import (
    ordered_active_serials,
    return_tracking,
    single_serial,
    tracking_values,
)


def _candidate(serial_no: str, *, received: datetime) -> StockCandidate:
    return StockCandidate(
        lot_name=f"LAYER-{serial_no}",
        item_code="PHONE",
        warehouse="Pool",
        location="Rivne",
        source_method="GSF_LAYER",
        relationship_model="OWN",
        fifo_datetime=received,
        receipt_name=f"PR-{serial_no}",
        receipt_row_index=1,
        available_qty=Decimal("1"),
        serial_no=serial_no,
    )


class SerialCandidateTests(TestCase):
    def test_exact_serial_overrides_fifo(self) -> None:
        candidates = [
            _candidate("111", received=datetime(2026, 8, 1, 8)),
            _candidate("112", received=datetime(2026, 8, 1, 9)),
            _candidate("113", received=datetime(2026, 8, 1, 10)),
        ]

        slices = allocate_global_fifo(
            candidates,
            item_code="PHONE",
            location="Rivne",
            qty=Decimal("1"),
            allowed_warehouses=frozenset({"Pool"}),
            serial_no="112",
        )

        self.assertEqual([(row.lot_name, row.serial_no) for row in slices], [("LAYER-112", "112")])

    def test_active_serial_count_must_match_the_ledger(self) -> None:
        self.assertEqual(
            ordered_active_serials(
                ("111", "112"),
                {"112", "111"},
                actual_qty=Decimal("2"),
                context="Layer L-1",
            ),
            ("111", "112"),
        )
        with self.assertRaises(GSFError):
            ordered_active_serials(
                ("111", "112"),
                {"112"},
                actual_qty=Decimal("2"),
                context="Layer L-1",
            )


class SerialCheckoutTests(TestCase):
    def test_pos_row_accepts_one_serial_only(self) -> None:
        self.assertEqual(single_serial(" 112\n"), "112")
        with self.assertRaises(GSFError):
            single_serial("112\n113")

    def test_stock_documents_keep_tracking_identity(self) -> None:
        self.assertEqual(
            tracking_values("112", None),
            {"use_serial_batch_fields": 1, "serial_no": "112"},
        )
        self.assertEqual(
            tracking_values(None, "B-1"),
            {"use_serial_batch_fields": 1, "batch_no": "B-1"},
        )


class SerialReturnTests(TestCase):
    def test_return_selects_the_exact_sold_serial(self) -> None:
        rows = {
            "POS-ROW": [
                SimpleNamespace(name="SI-111", qty=1, serial_no="111", batch_no=None),
                SimpleNamespace(name="SI-112", qty=1, serial_no="112", batch_no=None),
            ]
        }
        request = SimpleNamespace(
            return_against_item="POS-ROW",
            qty=1,
            serial_no="112",
            batch_no=None,
        )

        planned = consume_return_rows(rows, [request], {})

        self.assertEqual(
            [(row.sales_invoice_item, row.qty) for row in planned],
            [("SI-112", Decimal("1"))],
        )

    def test_return_rejects_another_serial(self) -> None:
        rows = {
            "POS-ROW": [SimpleNamespace(name="SI-112", qty=1, serial_no="112", batch_no=None)]
        }
        request = SimpleNamespace(
            return_against_item="POS-ROW",
            qty=1,
            serial_no="999",
            batch_no=None,
        )

        with self.assertRaises(GSFError):
            consume_return_rows(rows, [request], {})

    def test_return_layer_keeps_serial_lineage(self) -> None:
        self.assertEqual(return_tracking("112", None), ("SERIAL", None, "112"))
