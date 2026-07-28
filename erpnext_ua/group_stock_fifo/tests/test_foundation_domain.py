from unittest import TestCase

from erpnext_ua.group_stock_fifo.services.domain import (
    BindingRequest,
    GroupMemberFacts,
    GSFError,
    LaneFacts,
    ReadinessReport,
    WarehouseFacts,
    check_lane_available,
    validate_binding,
    validate_group,
)


def warehouse(name="Pool - A", company="FOP A", *, is_group=False, disabled=False):
    return WarehouseFacts(name=name, company=company, is_group=is_group, disabled=disabled)


def binding(**overrides):
    values = {
        "warehouse": warehouse(),
        "company": "FOP A",
        "warehouse_role": "GSF_OWN_POOL",
    }
    values.update(overrides)
    return BindingRequest(**values)


class WarehouseBindingTests(TestCase):
    """§8.2 exclusivity and the §7.4 warehouse rules."""

    def test_accepts_a_clean_leaf_warehouse(self) -> None:
        validate_binding(binding())

    def test_rejects_a_group_warehouse(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_binding(binding(warehouse=warehouse(is_group=True)))
        self.assertEqual(caught.exception.code, "WAREHOUSE_DOMAIN_CONFLICT")

    def test_rejects_a_company_mismatch(self) -> None:
        with self.assertRaises(GSFError):
            validate_binding(binding(warehouse=warehouse(company="FOP B")))

    def test_a_commission_warehouse_reports_its_own_code(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_binding(binding(existing_binding_app="CC"))
        self.assertEqual(caught.exception.code, "CC_WAREHOUSE_CONFLICT")

    def test_rejects_an_unknown_gsf_role(self) -> None:
        with self.assertRaises(GSFError):
            validate_binding(binding(warehouse_role="GSF_SOMETHING_ELSE"))

    def test_role_is_frozen_once_stock_has_moved(self) -> None:
        # §7.4: a warehouse cannot change role after its first stock movement.
        with self.assertRaises(GSFError):
            validate_binding(
                binding(
                    warehouse_role="GSF_SALE_STAGE",
                    previous_role="GSF_OWN_POOL",
                    has_stock_movements=True,
                )
            )

    def test_role_may_change_before_any_stock_moved(self) -> None:
        validate_binding(
            binding(warehouse_role="GSF_SALE_STAGE", previous_role="GSF_OWN_POOL")
        )


def member(company="FOP A", *, enabled=True, currency="UAH"):
    return GroupMemberFacts(
        company=company,
        enabled=enabled,
        can_source_stock=True,
        can_sell_stock=True,
        base_currency=currency,
    )


class CompanyGroupTests(TestCase):
    """§9.3 validations."""

    def test_accepts_three_companies_on_one_currency(self) -> None:
        validate_group(
            [member("FOP A"), member("FOP B"), member("FOP C")],
            group_currency="UAH",
            reporting_parent_company=None,
        )

    def test_rejects_a_duplicate_member(self) -> None:
        with self.assertRaises(GSFError):
            validate_group(
                [member("FOP A"), member("FOP A")],
                group_currency="UAH",
                reporting_parent_company=None,
            )

    def test_rejects_a_second_base_currency(self) -> None:
        with self.assertRaises(GSFError):
            validate_group(
                [member("FOP A"), member("FOP B", currency="EUR")],
                group_currency="UAH",
                reporting_parent_company=None,
            )

    def test_ignores_the_currency_of_a_disabled_member(self) -> None:
        validate_group(
            [member("FOP A"), member("FOP B", enabled=False, currency="EUR")],
            group_currency="UAH",
            reporting_parent_company=None,
        )

    def test_the_reporting_parent_cannot_also_trade(self) -> None:
        with self.assertRaises(GSFError):
            validate_group(
                [member("HUNTER"), member("FOP A")],
                group_currency="UAH",
                reporting_parent_company="HUNTER",
            )


class StagingLaneTests(TestCase):
    """§9.8 and ADR-006: zero balance is a precondition of the lock."""

    def test_an_available_empty_lane_can_be_taken(self) -> None:
        check_lane_available(LaneFacts("POS-1", "AVAILABLE", True), checkout="CHK-1")

    def test_a_lane_held_by_another_checkout_is_busy(self) -> None:
        with self.assertRaises(GSFError) as caught:
            check_lane_available(
                LaneFacts("POS-1", "LOCKED", True, current_checkout="CHK-9"), checkout="CHK-1"
            )
        self.assertEqual(caught.exception.code, "STAGE_LANE_BUSY")

    def test_the_same_checkout_may_reacquire_its_lane(self) -> None:
        check_lane_available(
            LaneFacts("POS-1", "LOCKED", True, current_checkout="CHK-1"), checkout="CHK-1"
        )

    def test_a_dirty_lane_is_never_reused_automatically(self) -> None:
        with self.assertRaises(GSFError) as caught:
            check_lane_available(LaneFacts("POS-1", "DIRTY", True), checkout="CHK-1")
        self.assertEqual(caught.exception.code, "STAGE_LANE_DIRTY")

    def test_leftover_stock_blocks_even_an_available_lane(self) -> None:
        # The status can lag; the balance is what decides.
        with self.assertRaises(GSFError) as caught:
            check_lane_available(
                LaneFacts("POS-1", "AVAILABLE", True, non_zero_items=("ITEM-1",)),
                checkout="CHK-1",
            )
        self.assertEqual(caught.exception.code, "STAGE_LANE_DIRTY")

    def test_a_disabled_lane_is_not_offered(self) -> None:
        with self.assertRaises(GSFError):
            check_lane_available(LaneFacts("POS-1", "AVAILABLE", False), checkout="CHK-1")


class ReadinessReportTests(TestCase):
    """§30.1: the gate opens only on an empty blocking list."""

    def test_a_clean_report_is_ready(self) -> None:
        report = ReadinessReport()
        self.assertTrue(report.ready)
        self.assertEqual(report.status, "ready_for_acceptance")

    def test_warnings_alone_do_not_block(self) -> None:
        report = ReadinessReport()
        report.warn("no staging lane for FOP C")
        self.assertTrue(report.ready)

    def test_one_blocking_check_is_enough(self) -> None:
        report = ReadinessReport()
        report.block("warehouse bound twice")
        self.assertFalse(report.ready)
        self.assertEqual(report.status, "blocked")
        self.assertEqual(report.as_dict()["blocking_checks"], ["warehouse bound twice"])
