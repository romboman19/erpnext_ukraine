from dataclasses import replace
from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.ownership import (
    OwnershipDispositionRequest,
    OwnershipPlanError,
    plan_ownership_disposition,
)


class OwnershipDispositionTests(TestCase):
    def request(self, **overrides) -> OwnershipDispositionRequest:
        request = OwnershipDispositionRequest(
            event_id="CONV-001",
            item_code="ITEM-001",
            relationship_model="COMMISSION",
            source_lot="COMMISSION-LOT",
            source_warehouse="Commission - CO",
            available_qty=Decimal("3"),
            convert_qty=Decimal("2"),
            return_qty=Decimal("1"),
            target_lot="OWN-LOT",
            target_warehouse="Own - CO",
            unit_cost=Decimal("80"),
        )
        return replace(request, **overrides)

    def test_commission_plan_separates_conversion_purchase_and_partner_return(self) -> None:
        plan = plan_ownership_disposition(self.request())

        self.assertEqual(plan.converted_qty, Decimal("2"))
        self.assertEqual(plan.returned_qty, Decimal("1"))
        self.assertEqual(plan.remaining_qty, Decimal("0"))
        self.assertEqual(plan.obligation_amount, Decimal("160"))
        self.assertEqual(plan.base_asset_value, Decimal("160"))
        self.assertEqual(
            [movement.kind for movement in plan.movements],
            [
                "REMOVE_THIRD_PARTY_FOR_CONVERSION",
                "RECEIVE_OWN_BY_PURCHASE",
                "RETURN_TO_PARTNER",
            ],
        )

    def test_foreign_currency_plan_values_own_stock_in_company_currency(self) -> None:
        plan = plan_ownership_disposition(
            self.request(
                available_qty=Decimal("2"),
                convert_qty=Decimal("2"),
                return_qty=Decimal("0"),
                unit_cost=Decimal("10"),
                currency="USD",
                exchange_rate=Decimal("40"),
            )
        )

        self.assertEqual(plan.obligation_amount, Decimal("20"))
        self.assertEqual(plan.base_asset_value, Decimal("800"))

    def test_consignment_stock_uses_the_same_explicit_purchase_boundary(self) -> None:
        plan = plan_ownership_disposition(
            self.request(relationship_model="CONSIGNMENT", convert_qty=Decimal("1"), return_qty=Decimal("0"))
        )

        self.assertEqual(plan.relationship_model, "CONSIGNMENT")
        self.assertEqual(plan.movements[1].kind, "RECEIVE_OWN_BY_PURCHASE")

    def test_plan_rejects_more_disposed_stock_than_available(self) -> None:
        with self.assertRaisesRegex(OwnershipPlanError, "exceed available"):
            plan_ownership_disposition(self.request(convert_qty=Decimal("3"), return_qty=Decimal("1")))

    def test_plan_rejects_conversion_without_distinct_own_coordinates(self) -> None:
        with self.assertRaisesRegex(OwnershipPlanError, "distinct own-stock"):
            plan_ownership_disposition(self.request(target_lot="COMMISSION-LOT"))

        with self.assertRaisesRegex(OwnershipPlanError, "own-stock warehouse"):
            plan_ownership_disposition(self.request(target_warehouse="Commission - CO"))

    def test_serialized_disposition_requires_exact_disjoint_selections(self) -> None:
        plan = plan_ownership_disposition(
            self.request(
                convert_qty=Decimal("1"),
                return_qty=Decimal("1"),
                convert_serial_numbers=("SER-001",),
                return_serial_numbers=("SER-002",),
            )
        )
        self.assertEqual(plan.movements[0].serial_numbers, ("SER-001",))
        self.assertEqual(plan.movements[-1].serial_numbers, ("SER-002",))

        with self.assertRaisesRegex(OwnershipPlanError, "cannot be converted and returned"):
            plan_ownership_disposition(
                self.request(
                    convert_qty=Decimal("1"),
                    return_qty=Decimal("1"),
                    convert_serial_numbers=("SER-001",),
                    return_serial_numbers=("SER-001",),
                )
            )

        with self.assertRaisesRegex(OwnershipPlanError, "count must equal"):
            plan_ownership_disposition(
                self.request(convert_qty=Decimal("2"), return_qty=Decimal("0"), convert_serial_numbers=("SER-001",))
            )
