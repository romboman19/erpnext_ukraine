"""Pure POS-UA return-planning tests; no site writes or stock documents."""

from decimal import Decimal
from unittest import TestCase

import frappe

from erpnext_ua.group_stock_fifo.services.domain import GSFError
from erpnext_ua.group_stock_fifo.services.pos_ua import _consume_return_rows


def _row(name: str, qty: float) -> frappe._dict:
    return frappe._dict(name=name, qty=qty)


def _request(pos_row: str, qty: float) -> frappe._dict:
    return frappe._dict(return_against_item=pos_row, qty=qty)


class ReturnSlicePlanningTests(TestCase):
    def test_partial_return_consumes_oldest_technical_rows_first(self) -> None:
        rows = {"POS-ROW-1": [_row("SI-A", 2), _row("SI-B", 3)]}
        planned = _consume_return_rows(
            rows,
            [_request("POS-ROW-1", 3)],
            {"SI-A": Decimal("1")},
        )

        self.assertEqual(
            [(line.sales_invoice_item, line.qty) for line in planned],
            [("SI-A", Decimal("1")), ("SI-B", Decimal("2"))],
        )

    def test_full_return_preserves_each_original_technical_row(self) -> None:
        rows = {"POS-ROW-1": [_row("SI-A", 2), _row("SI-B", 3)]}
        planned = _consume_return_rows(rows, [_request("POS-ROW-1", 5)], {})

        self.assertEqual(
            [(line.sales_invoice_item, line.qty) for line in planned],
            [("SI-A", Decimal("2")), ("SI-B", Decimal("3"))],
        )

    def test_previous_returns_are_not_allocated_twice(self) -> None:
        rows = {"POS-ROW-1": [_row("SI-A", 2), _row("SI-B", 3)]}
        planned = _consume_return_rows(
            rows,
            [_request("POS-ROW-1", 2)],
            {"SI-A": Decimal("2"), "SI-B": Decimal("1")},
        )

        self.assertEqual(
            [(line.sales_invoice_item, line.qty) for line in planned],
            [("SI-B", Decimal("2"))],
        )

    def test_over_return_is_rejected(self) -> None:
        rows = {"POS-ROW-1": [_row("SI-A", 2)]}

        with self.assertRaisesRegex(GSFError, "only 1 returnable"):
            _consume_return_rows(
                rows,
                [_request("POS-ROW-1", 2)],
                {"SI-A": Decimal("1")},
            )
