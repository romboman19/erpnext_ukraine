from decimal import Decimal
from unittest import TestCase

from erpnext_ua.group_stock_fifo.services.fulfillment_benefits import split_route_amount
from erpnext_ua.group_stock_fifo.services.fulfillment_domain import (
    FulfillmentRouteKey,
    ProviderAllocationRef,
    effective_rate,
    split_discount,
)


class TestFulfillmentRoute(TestCase):
    def setUp(self) -> None:
        self.route = FulfillmentRouteKey(
            provider_id="GSF",
            seller_company="FOP A",
            provider_location="Kyiv",
            legal_entity_type="Company",
            legal_entity_name="FOP A",
            fiscal_route="FISCAL",
        )

    def test_route_identity_is_stable_and_covers_legal_coordinates(self) -> None:
        self.assertEqual(self.route.stable_id, self.route.stable_id)
        changed = FulfillmentRouteKey(
            provider_id=self.route.provider_id,
            seller_company="FOP B",
            provider_location=self.route.provider_location,
            legal_entity_type=self.route.legal_entity_type,
            legal_entity_name="FOP B",
            fiscal_route=self.route.fiscal_route,
        )
        self.assertNotEqual(self.route.stable_id, changed.stable_id)

    def test_manifest_serialization_preserves_money_exactly(self) -> None:
        ref = ProviderAllocationRef(
            route=self.route,
            allocation_doctype="GSF Allocation",
            allocation_name="ALLOC-1",
            item_code="ITEM-1",
            qty=Decimal("1.250"),
            external_row_id="ROW-1",
            rate=Decimal("1500.00"),
            discount_amount=Decimal("0.01"),
        )
        values = ref.as_dict()
        self.assertEqual(values["qty"], "1.250")
        self.assertEqual(values["rate"], "1500.00")
        self.assertEqual(values["discount_amount"], "0.01")
        self.assertEqual(values["route"]["seller_company"], "FOP A")


class TestFulfillmentMoney(TestCase):
    def test_discount_follows_route_quantities_and_keeps_the_tail(self) -> None:
        result = split_discount(
            Decimal("10.00"),
            [Decimal("1"), Decimal("2"), Decimal("3")],
        )
        self.assertEqual(sum(result), Decimal("10.00"))
        self.assertEqual(result[0], Decimal("10.00") / Decimal("6"))
        self.assertEqual(result[-1], Decimal("5.00"))

    def test_invalid_discount_split_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            split_discount(Decimal("-0.01"), [Decimal("1")])
        with self.assertRaises(ValueError):
            split_discount(Decimal("1"), [])

    def test_effective_rate_preserves_the_visible_line_total(self) -> None:
        rate = effective_rate(
            qty=Decimal("4"),
            rate=Decimal("1500"),
            discount_amount=Decimal("600"),
        )
        self.assertEqual(rate, Decimal("1350"))

    def test_effective_rate_rejects_over_discount(self) -> None:
        with self.assertRaises(ValueError):
            effective_rate(
                qty=Decimal("1"),
                rate=Decimal("100"),
                discount_amount=Decimal("100.01"),
            )

    def test_benefit_route_split_assigns_rounding_tail_deterministically(self) -> None:
        result = split_route_amount(
            Decimal("10.00"),
            {
                "ROUTE-A": Decimal("1"),
                "ROUTE-B": Decimal("1"),
                "ROUTE-C": Decimal("1"),
            },
        )
        self.assertEqual(
            result,
            {"ROUTE-A": Decimal("3.33"), "ROUTE-B": Decimal("3.33"), "ROUTE-C": Decimal("3.34")},
        )
