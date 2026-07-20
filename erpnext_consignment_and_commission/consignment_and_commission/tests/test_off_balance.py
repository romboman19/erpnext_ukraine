import sys
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from ..integrations.off_balance import (
    _movement_parts,
    _rounded_amount,
    _serials,
    cancel_reference_off_balance,
)


class _Database:
    @staticmethod
    def get_single_value(_doctype: str, _fieldname: str) -> int:
        return 2


class _Frappe:
    db = _Database()


class _Entry:
    ignore_linked_doctypes = ("Existing Audit",)

    def __init__(self) -> None:
        self.cancelled = False

    def get(self, fieldname: str):
        return getattr(self, fieldname)

    def cancel(self) -> None:
        self.cancelled = True


class _CancellationFrappe:
    def __init__(self, entry: _Entry) -> None:
        self.entry = entry
        self.filters = None

    def get_all(self, _doctype: str, *, filters, **_kwargs):
        self.filters = filters
        return ["UA-OB-00001"]

    def get_doc(self, _doctype: str, _name: str) -> _Entry:
        return self.entry


class _ReferenceDocument:
    doctype = "CC Receipt"
    name = "CC-RCP-00001"


class TestOffBalanceAmounts(TestCase):
    def test_rounds_accounting_amount_with_system_currency_precision(self) -> None:
        self.assertEqual(
            _rounded_amount(_Frappe(), "33.333", "3"),
            Decimal("100.00"),
        )

    def test_serial_split_keeps_the_exact_total_as_a_last_unit_residual(self) -> None:
        parts = _movement_parts(
            _Frappe(),
            qty=Decimal("3"),
            amount=Decimal("100.00"),
            serial_numbers=("S-1", "S-2", "S-3"),
        )
        self.assertEqual(
            parts,
            (
                (Decimal("1"), Decimal("33.33"), "S-1"),
                (Decimal("1"), Decimal("33.33"), "S-2"),
                (Decimal("1"), Decimal("33.34"), "S-3"),
            ),
        )

    def test_serial_split_rejects_quantity_identity_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "one Serial No per unit"):
            _movement_parts(
                _Frappe(),
                qty=Decimal("2"),
                amount=Decimal("100"),
                serial_numbers=("S-1",),
            )

    def test_serial_split_rejects_unrepresentable_positive_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "too small"):
            _movement_parts(
                _Frappe(),
                qty=Decimal("3"),
                amount=Decimal("0.02"),
                serial_numbers=("S-1", "S-2", "S-3"),
            )

    def test_serial_parser_normalizes_blank_lines(self) -> None:
        self.assertEqual(_serials(" S-1\n\nS-2 \n"), ("S-1", "S-2"))

    def test_cancellation_ignores_the_parent_link_on_the_ledger_entry(self) -> None:
        entry = _Entry()
        fake_frappe = _CancellationFrappe(entry)

        with patch.dict(sys.modules, {"frappe": fake_frappe}):
            cancel_reference_off_balance(_ReferenceDocument())

        self.assertEqual(
            entry.ignore_linked_doctypes,
            ("CC Receipt", "Existing Audit"),
        )
        self.assertTrue(entry.cancelled)
        self.assertEqual(
            fake_frappe.filters,
            {
                "reference_doctype": "CC Receipt",
                "reference_name": "CC-RCP-00001",
                "docstatus": 1,
            },
        )
