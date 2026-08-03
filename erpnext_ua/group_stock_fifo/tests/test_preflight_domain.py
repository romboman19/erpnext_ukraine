"""Unit tests for the §17.2 preflight decision.

Only the decision is tested here. Gathering the facts needs a site, and the
value prediction deliberately calls ERPNext's own `FIFOValuation` rather than a
reimplementation, so there is nothing to unit-test there that would not be
testing a mock of the thing that matters.
"""

from decimal import Decimal
from unittest import TestCase

from erpnext_ua.group_stock_fifo.services.domain import GSFError
from erpnext_ua.group_stock_fifo.services.preflight import (
    PreflightReport,
    QueueFacts,
    assert_ok,
    evaluate,
)


def facts(**overrides):
    values = {
        "item_code": "ITEM-1",
        "warehouse": "Pool A",
        "requested_qty": Decimal("2"),
        "selected": {"GSFL-a": Decimal("2")},
        "expected": {"GSFL-a": Decimal("2")},
    }
    values.update(overrides)
    return QueueFacts(**values)


class AgreementTests(TestCase):
    def test_a_plan_matching_the_queue_passes(self) -> None:
        self.assertTrue(evaluate(facts()).ok)

    def test_quantities_compare_by_value_not_spelling(self) -> None:
        report = evaluate(
            facts(selected={"GSFL-a": Decimal("2.00")}, expected={"GSFL-a": Decimal("2")})
        )
        self.assertTrue(report.ok)

    def test_a_multi_layer_plan_in_queue_order_passes(self) -> None:
        plan = {"GSFL-a": Decimal("2"), "GSFL-b": Decimal("3")}
        self.assertTrue(evaluate(facts(selected=plan, expected=dict(plan))).ok)


class DivergenceTests(TestCase):
    """Gate 0c in one assertion: the dimension does not steer the queue."""

    def test_older_unselected_stock_is_refused(self) -> None:
        report = evaluate(
            facts(
                selected={"GSFL-new": Decimal("2")},
                expected={"GSFL-old": Decimal("1"), "GSFL-new": Decimal("1")},
            )
        )
        self.assertEqual(report.error_code, "VALUATION_QUEUE_DIVERGENCE")

    def test_the_refusal_names_the_layers(self) -> None:
        report = evaluate(
            facts(
                selected={"GSFL-new": Decimal("2")},
                expected={"GSFL-old": Decimal("2")},
            )
        )
        joined = " ".join(report.reasons)
        self.assertIn("GSFL-old", joined)
        self.assertIn("GSFL-new", joined)

    def test_a_partial_quantity_mismatch_is_refused(self) -> None:
        report = evaluate(
            facts(selected={"GSFL-a": Decimal("2")}, expected={"GSFL-a": Decimal("1")})
        )
        self.assertEqual(report.error_code, "VALUATION_QUEUE_DIVERGENCE")
        self.assertIn("GSF selected 2, ERPNext would consume 1", " ".join(report.reasons))


class WarehouseStateTests(TestCase):
    """Three states in which no prediction holds, reported before the comparison."""

    def test_unclassified_stock_is_reported_first(self) -> None:
        report = evaluate(
            facts(
                unclassified_qty=Decimal("1"),
                selected={"GSFL-a": Decimal("2")},
                expected={"GSFL-b": Decimal("2")},
            )
        )
        self.assertEqual(report.error_code, "UNCLASSIFIED_GSF_STOCK")

    def test_a_pending_repost_blocks(self) -> None:
        self.assertEqual(evaluate(facts(pending_repost=True)).error_code, "PENDING_REPOST")

    def test_negative_stock_blocks(self) -> None:
        self.assertEqual(evaluate(facts(negative_stock=True)).error_code, "NEGATIVE_STOCK_RISK")

    def test_a_settled_warehouse_does_not_block(self) -> None:
        self.assertTrue(
            evaluate(
                facts(unclassified_qty=Decimal("0"), pending_repost=False, negative_stock=False)
            ).ok
        )


class AssertOkTests(TestCase):
    def test_all_clear_passes(self) -> None:
        assert_ok([evaluate(facts()), evaluate(facts(warehouse="Pool B"))])

    def test_the_first_refusal_carries_its_code_outwards(self) -> None:
        reports = [
            evaluate(facts()),
            evaluate(facts(warehouse="Pool B", negative_stock=True)),
        ]
        with self.assertRaises(GSFError) as caught:
            assert_ok(reports)
        self.assertEqual(caught.exception.code, "NEGATIVE_STOCK_RISK")
        self.assertIn("Pool B", str(caught.exception))

    def test_an_empty_list_is_not_a_pass_by_accident(self) -> None:
        """Nothing to check is fine; the caller decides whether that is expected."""
        assert_ok([])


class ReportTests(TestCase):
    def test_a_clean_report_is_ok(self) -> None:
        self.assertTrue(
            PreflightReport(
                warehouse="Pool A",
                item_code="ITEM-1",
                requested_qty=Decimal("2"),
                predicted_value=Decimal("2000"),
            ).ok
        )

    def test_any_error_code_makes_it_not_ok(self) -> None:
        report = PreflightReport(
            warehouse="Pool A",
            item_code="ITEM-1",
            requested_qty=Decimal("2"),
            predicted_value=Decimal("2000"),
            error_code="VALUATION_QUEUE_DIVERGENCE",
        )
        self.assertFalse(report.ok)
