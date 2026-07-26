from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.sale import (
    ManagedSaleError,
    ManagedSaleLine,
    ManagedSaleRequest,
    managed_sale_fingerprint,
    validate_managed_sale_request,
)


class ManagedSalePolicyTests(TestCase):
    def test_fingerprint_is_stable_across_allocation_order(self) -> None:
        left = self.request(
            lines=(
                ManagedSaleLine("ALLOC-1", Decimal("100")),
                ManagedSaleLine("ALLOC-2", Decimal("50")),
            )
        )
        right = self.request(lines=tuple(reversed(left.lines)))

        self.assertEqual(managed_sale_fingerprint(left), managed_sale_fingerprint(right))

    def test_fingerprint_changes_with_customer_rate_or_posting_date(self) -> None:
        base = managed_sale_fingerprint(self.request())

        self.assertNotEqual(base, managed_sale_fingerprint(self.request(customer="Other")))
        self.assertNotEqual(
            base,
            managed_sale_fingerprint(
                self.request(lines=(ManagedSaleLine("ALLOC-1", Decimal("101")),))
            ),
        )
        self.assertNotEqual(
            base,
            managed_sale_fingerprint(self.request(posting_date="2026-07-14")),
        )
        self.assertNotEqual(
            base,
            managed_sale_fingerprint(
                self.request(currency="USD", conversion_rate=Decimal("41.25"))
            ),
        )

    def test_request_rejects_duplicate_allocations_and_invalid_rates(self) -> None:
        with self.assertRaisesRegex(ManagedSaleError, "unique"):
            validate_managed_sale_request(
                self.request(
                    lines=(
                        ManagedSaleLine("ALLOC-1", Decimal("100")),
                        ManagedSaleLine("ALLOC-1", Decimal("100")),
                    )
                )
            )
        with self.assertRaisesRegex(ManagedSaleError, "non-negative"):
            validate_managed_sale_request(
                self.request(lines=(ManagedSaleLine("ALLOC-1", Decimal("-1")),))
            )
        with self.assertRaisesRegex(ManagedSaleError, "provided together"):
            validate_managed_sale_request(self.request(currency="USD"))
        with self.assertRaisesRegex(ManagedSaleError, "positive"):
            validate_managed_sale_request(
                self.request(currency="USD", conversion_rate=Decimal("0"))
            )

    @staticmethod
    def request(**overrides: object) -> ManagedSaleRequest:
        values = {
            "idempotency_key": "CHECKOUT-1:SALE-1",
            "customer": "Customer",
            "lines": (ManagedSaleLine("ALLOC-1", Decimal("100")),),
            "posting_date": "2026-07-13",
        }
        values.update(overrides)
        return ManagedSaleRequest(**values)  # type: ignore[arg-type]
