"""GSF technical invoice rows must remain one visible fiscal receipt line."""

from unittest import TestCase

import frappe

from erpnext_ua.ua_fiscal.sales_invoice import _group_gsf_lines


class GSFReceiptGroupingTests(TestCase):
    def test_fifo_slices_are_merged_without_losing_totals(self) -> None:
        invoice = frappe._dict(
            items=[
                frappe._dict(name="ROW-1", gsf_display_group="ALLOC-1"),
                frappe._dict(name="ROW-2", gsf_display_group="ALLOC-1"),
            ]
        )
        lines = [
            self._line(qty=2, amount=1800, subtotal=2000, discount_sum=200),
            self._line(qty=1, amount=900, subtotal=1000, discount_sum=100),
        ]

        grouped = _group_gsf_lines(invoice, lines)

        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["qty"], 3)
        self.assertEqual(grouped[0]["amount"], 2700)
        self.assertEqual(grouped[0]["subtotal"], 3000)
        self.assertEqual(grouped[0]["discount_sum"], 300)
        self.assertEqual(grouped[0]["discount_percent"], 10)

    def test_different_fiscal_identity_is_rejected(self) -> None:
        invoice = frappe._dict(
            items=[
                frappe._dict(name="ROW-1", gsf_display_group="ALLOC-1"),
                frappe._dict(name="ROW-2", gsf_display_group="ALLOC-1"),
            ]
        )
        lines = [self._line(qty=1, amount=1000), self._line(qty=1, amount=1100)]
        lines[1]["price"] = 1100

        with self.assertRaises(frappe.ValidationError):
            _group_gsf_lines(invoice, lines)

    @staticmethod
    def _line(qty: float, amount: float, subtotal: float = 0, discount_sum: float = 0) -> dict:
        line = {
            "code": "ITEM-1",
            "barcode": "482000000001",
            "uktzed": None,
            "dkpp": None,
            "unit_cd": None,
            "letters": "А",
            "name": "Товар",
            "uom": "Nos",
            "qty": qty,
            "price": 1000,
            "amount": amount,
        }
        if discount_sum:
            line.update(
                {
                    "discount_type": 0,
                    "subtotal": subtotal,
                    "discount_percent": discount_sum * 100 / subtotal,
                    "discount_sum": discount_sum,
                }
            )
        return line
