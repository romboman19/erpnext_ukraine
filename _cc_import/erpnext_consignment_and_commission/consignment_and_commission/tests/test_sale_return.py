from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.sale_return import (
    ManagedReturnError,
    ManagedReturnLine,
    ManagedReturnRequest,
    calculate_return_financial_delta,
    managed_return_fingerprint,
    validate_return_request,
)


class ManagedReturnTests(TestCase):
    def request(self, **overrides: object) -> ManagedReturnRequest:
        values = {
            "idempotency_key": "RETURN-1",
            "posting_date": date(2026, 7, 31),
            "lines": (
                ManagedReturnLine("SALE-1", Decimal("1")),
                ManagedReturnLine("SALE-2", Decimal("2")),
            ),
        }
        values.update(overrides)
        return ManagedReturnRequest(**values)  # type: ignore[arg-type]

    def test_fingerprint_is_line_order_independent(self) -> None:
        first = self.request()
        reordered = self.request(lines=tuple(reversed(first.lines)))
        self.assertEqual(managed_return_fingerprint(first), managed_return_fingerprint(reordered))

    def test_duplicate_or_nonpositive_lines_are_rejected(self) -> None:
        with self.assertRaisesRegex(ManagedReturnError, "unique"):
            validate_return_request(
                self.request(
                    lines=(
                        ManagedReturnLine("SALE-1", Decimal("1")),
                        ManagedReturnLine("SALE-1", Decimal("1")),
                    )
                )
            )
        with self.assertRaisesRegex(ManagedReturnError, "positive"):
            validate_return_request(
                self.request(lines=(ManagedReturnLine("SALE-1", Decimal("0")),))
            )

    def test_partial_return_rounding_closes_commission_sale_exactly(self) -> None:
        deltas = []
        returned = Decimal("0")
        for _index in range(3):
            delta = calculate_return_financial_delta(
                relationship_model="COMMISSION",
                sold_qty=3,
                returned_qty_before=returned,
                return_qty=1,
                gross_amount="100.00",
                commission_amount="15.00",
                partner_amount="85.00",
            )
            deltas.append(delta)
            returned = delta.cumulative_qty
        self.assertEqual([row.gross_amount for row in deltas], [
            Decimal("33.33"),
            Decimal("33.34"),
            Decimal("33.33"),
        ])
        self.assertEqual(sum((row.gross_amount for row in deltas), Decimal("0")), Decimal("100.00"))
        self.assertEqual(
            sum((row.commission_amount for row in deltas), Decimal("0")),
            Decimal("15.00"),
        )
        self.assertEqual(sum((row.partner_amount for row in deltas), Decimal("0")), Decimal("85.00"))

    def test_partial_return_rounding_closes_consignment_debt_exactly(self) -> None:
        first = calculate_return_financial_delta(
            relationship_model="CONSIGNMENT",
            sold_qty=3,
            returned_qty_before=0,
            return_qty=1,
            gross_amount="100.00",
            commission_amount=0,
            partner_amount="70.00",
        )
        second = calculate_return_financial_delta(
            relationship_model="CONSIGNMENT",
            sold_qty=3,
            returned_qty_before=1,
            return_qty=2,
            gross_amount="100.00",
            commission_amount=0,
            partner_amount="70.00",
        )
        self.assertEqual(first.partner_amount, Decimal("23.33"))
        self.assertEqual(second.partner_amount, Decimal("46.67"))
        self.assertEqual(first.partner_amount + second.partner_amount, Decimal("70.00"))

    def test_return_delta_rejects_over_return_and_unbalanced_snapshot(self) -> None:
        with self.assertRaisesRegex(ManagedReturnError, "remaining"):
            calculate_return_financial_delta(
                relationship_model="OWN",
                sold_qty=1,
                returned_qty_before=1,
                return_qty=1,
                gross_amount=10,
                commission_amount=0,
                partner_amount=0,
            )
        with self.assertRaisesRegex(ManagedReturnError, "not balanced"):
            calculate_return_financial_delta(
                relationship_model="COMMISSION",
                sold_qty=1,
                returned_qty_before=0,
                return_qty=1,
                gross_amount=10,
                commission_amount=1,
                partner_amount=8,
            )
