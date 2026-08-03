from decimal import Decimal
from unittest import TestCase

from erpnext_ua.ua_gift_certificates.domain.allocation import EligibleLine, allocate_proportionally, restore_share
from erpnext_ua.ua_gift_certificates.domain.funding import initial_funding, split_consumption
from erpnext_ua.ua_gift_certificates.domain.lifecycle import ensure_redeemable, status_after_balance
from erpnext_ua.ua_gift_certificates.domain.token import generate_token, masked, token_hash, validate_token


class TestGiftCertificateDomain(TestCase):
    def test_token_has_checksum_and_hmac_identity(self):
        material = generate_token()

        self.assertEqual(validate_token(f"  {material.token.lower()}  "), material.token)
        self.assertEqual(len(token_hash(material.token, "test-secret")), 64)
        self.assertEqual(masked(material.last4), f"••••{material.last4}")

        corrupted = material.token[:-1] + ("A" if material.token[-1] != "A" else "B")
        with self.assertRaises(ValueError):
            validate_token(corrupted)

    def test_discounted_and_premium_funding_are_separate(self):
        discounted = initial_funding("1000", "800")
        premium = initial_funding("1000", "1100")

        self.assertEqual(discounted.paid, Decimal("800.00"))
        self.assertEqual(discounted.promotional, Decimal("200.00"))
        self.assertEqual(discounted.premium, Decimal("0.00"))
        self.assertEqual(premium.paid, Decimal("1000.00"))
        self.assertEqual(premium.premium, Decimal("100.00"))

    def test_component_consumption_policies_are_exact(self):
        proportional = split_consumption("800", "200", "333.33", "Proportional")
        paid_first = split_consumption("800", "200", "900", "Paid First")
        promotional_first = split_consumption("800", "200", "350", "Promotional First")

        self.assertEqual(proportional.paid + proportional.promotional, Decimal("333.33"))
        self.assertEqual((paid_first.paid, paid_first.promotional), (Decimal("800.00"), Decimal("100.00")))
        self.assertEqual(
            (promotional_first.paid, promotional_first.promotional),
            (Decimal("150.00"), Decimal("200.00")),
        )

    def test_item_allocation_preserves_total_and_capacity(self):
        rows = allocate_proportionally(
            "100.00",
            [EligibleLine("A", Decimal("33.33")), EligibleLine("B", Decimal("66.67"))],
        )

        self.assertEqual(sum((row.amount for row in rows), Decimal("0")), Decimal("100.00"))
        self.assertEqual(rows[0].amount, Decimal("33.33"))
        self.assertEqual(rows[1].amount, Decimal("66.67"))

    def test_three_partial_returns_restore_rounding_residual(self):
        first = restore_share("100", 1, 3, "0", 0)
        second = restore_share("100", 1, 3, first, 1)
        third = restore_share("100", 1, 3, first + second, 2)

        self.assertEqual((first, second, third), (Decimal("33.33"), Decimal("33.33"), Decimal("33.34")))
        self.assertEqual(first + second + third, Decimal("100.00"))

    def test_lifecycle_is_fail_closed(self):
        self.assertEqual(
            status_after_balance(balance="40", redeemed_total="60", usage_policy="Multi Use Balance"),
            "Partially Redeemed",
        )
        with self.assertRaisesRegex(Exception, "expired"):
            ensure_redeemable("Expired")
        with self.assertRaisesRegex(Exception, "manual review"):
            ensure_redeemable("Payment Pending")
