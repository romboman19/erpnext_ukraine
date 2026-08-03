from decimal import Decimal

from frappe.tests import UnitTestCase

from erpnext_ua.ua_loyalty.domain.allocations import RedemptionLine, allocate_redemption
from erpnext_ua.ua_loyalty.domain.balances import apply_credit, balances
from erpnext_ua.ua_loyalty.domain.returns import calculate_return_share
from erpnext_ua.ua_loyalty.domain.tiers import Tier, select_tier


class TestLoyaltyDomain(UnitTestCase):
    def test_thresholds_use_metric_before_sale(self):
        tiers = (
            Tier("T3", Decimal("500"), Decimal("3")),
            Tier("T5", Decimal("10000"), Decimal("5")),
            Tier("T7", Decimal("40000"), Decimal("7")),
            Tier("T10", Decimal("80000"), Decimal("10")),
        )
        self.assertEqual(select_tier(Decimal("499.99"), tiers).rate, Decimal("0"))
        self.assertEqual(select_tier(Decimal("500"), tiers).rate, Decimal("3"))
        self.assertEqual(select_tier(Decimal("10000"), tiers).rate, Decimal("5"))
        self.assertEqual(select_tier(Decimal("80000"), tiers).rate, Decimal("10"))

    def test_negative_balance_is_not_floored(self):
        state = balances(Decimal("20"), Decimal("0"), Decimal("0"))
        self.assertEqual(state.marketing, Decimal("20.00"))
        state = balances(state.marketing - Decimal("60"), Decimal("0"), Decimal("0"))
        self.assertEqual(state.marketing, Decimal("-40.00"))
        self.assertEqual(state.redeemable, Decimal("0.00"))
        self.assertEqual(state.debt, Decimal("40.00"))

    def test_new_credit_offsets_debt_before_becoming_redeemable(self):
        first = apply_credit(Decimal("-40"), Decimal("25"))
        self.assertEqual(first.balance_after, Decimal("-15.00"))
        self.assertEqual(first.debt_offset, Decimal("25.00"))
        self.assertEqual(first.redeemable_credit, Decimal("0.00"))
        second = apply_credit(first.balance_after, Decimal("30"))
        self.assertEqual(second.balance_after, Decimal("15.00"))
        self.assertEqual(second.debt_offset, Decimal("15.00"))
        self.assertEqual(second.redeemable_credit, Decimal("15.00"))

    def test_redemption_allocates_in_stable_row_order(self):
        rows = (
            RedemptionLine("ROW-1", Decimal("60")),
            RedemptionLine("ROW-2", Decimal("60")),
            RedemptionLine("ROW-3", Decimal("60")),
        )
        result = allocate_redemption(Decimal("100"), rows)
        self.assertEqual(tuple(row.amount for row in result), (Decimal("60.00"), Decimal("40.00"), Decimal("0.00")))

    def test_partial_return_uses_exact_final_residual(self):
        first = calculate_return_share(
            original_amount=Decimal("10"),
            original_qty=Decimal("3"),
            return_qty=Decimal("1"),
            previous_return_qty=Decimal("0"),
            previous_amount=Decimal("0"),
        )
        second = calculate_return_share(
            original_amount=Decimal("10"),
            original_qty=Decimal("3"),
            return_qty=Decimal("1"),
            previous_return_qty=Decimal("1"),
            previous_amount=first,
        )
        third = calculate_return_share(
            original_amount=Decimal("10"),
            original_qty=Decimal("3"),
            return_qty=Decimal("1"),
            previous_return_qty=Decimal("2"),
            previous_amount=first + second,
        )
        self.assertEqual((first, second, third), (Decimal("3.33"), Decimal("3.33"), Decimal("3.34")))
        self.assertEqual(first + second + third, Decimal("10.00"))

    def test_return_cannot_exceed_original_quantity(self):
        with self.assertRaises(ValueError):
            calculate_return_share(
                original_amount=Decimal("10"),
                original_qty=Decimal("3"),
                return_qty=Decimal("2"),
                previous_return_qty=Decimal("2"),
                previous_amount=Decimal("6.67"),
            )
