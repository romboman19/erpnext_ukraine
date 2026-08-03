from unittest import TestCase

from erpnext_ua.group_stock_fifo.setup.cc_discovery import (
    CCLocationSnapshot,
    plan_cc_bindings,
)


def location(
    name: str = "Kyiv",
    *,
    company: str = "FOP A",
    disabled: bool = False,
    physical_location: str | None = "SHOP-1",
    own: str = "Own - A",
    commission: str = "Commission - A",
    consignment: str = "Consignment - A",
) -> CCLocationSnapshot:
    return CCLocationSnapshot(
        name=name,
        company=company,
        disabled=disabled,
        physical_location=physical_location,
        own_warehouse=own,
        commission_warehouse=commission,
        consignment_warehouse=consignment,
    )


class CCDiscoveryPlanTests(TestCase):
    def test_maps_every_cc_warehouse_to_its_read_only_role(self) -> None:
        plan = plan_cc_bindings([location()])

        self.assertFalse(plan.conflicts)
        self.assertEqual(
            [(row.warehouse, row.warehouse_role) for row in plan.bindings],
            [
                ("Commission - A", "CC_COMMISSION"),
                ("Consignment - A", "CC_CONSIGNMENT"),
                ("Own - A", "CC_OWN"),
            ],
        )
        self.assertTrue(all(row.enabled for row in plan.bindings))

    def test_keeps_disabled_locations_registered_but_inactive(self) -> None:
        plan = plan_cc_bindings([location(disabled=True)])

        self.assertEqual(len(plan.bindings), 3)
        self.assertTrue(all(not row.enabled for row in plan.bindings))

    def test_rejects_one_warehouse_claimed_by_two_locations(self) -> None:
        plan = plan_cc_bindings(
            [
                location(),
                location(
                    "Lviv",
                    company="FOP B",
                    own="Own - B",
                    commission="Commission - A",
                    consignment="Consignment - B",
                ),
            ]
        )

        self.assertNotIn("Commission - A", {row.warehouse for row in plan.bindings})
        self.assertEqual(len(plan.conflicts), 1)
        self.assertIn("both CC Location Kyiv", plan.conflicts[0])

    def test_never_reintroduces_a_warehouse_after_three_way_conflict(self) -> None:
        plan = plan_cc_bindings(
            [
                location("A", commission="Shared"),
                location(
                    "B",
                    company="FOP B",
                    own="Own - B",
                    commission="Shared",
                    consignment="Consignment - B",
                ),
                location(
                    "C",
                    company="FOP C",
                    own="Own - C",
                    commission="Shared",
                    consignment="Consignment - C",
                ),
            ]
        )

        self.assertNotIn("Shared", {row.warehouse for row in plan.bindings})
        self.assertEqual(len(plan.conflicts), 2)
