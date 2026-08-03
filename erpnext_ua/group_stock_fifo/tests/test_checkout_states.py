"""Unit tests for the §23 checkout state machine."""

from unittest import TestCase

from erpnext_ua.group_stock_fifo.services import checkout_states as states
from erpnext_ua.group_stock_fifo.services.domain import GSFError


class HappyPathTests(TestCase):
    def test_the_whole_non_fiscal_walk(self) -> None:
        for current, target in (
            (states.DRAFT, states.RESERVING),
            (states.RESERVING, states.RESERVED),
            (states.RESERVED, states.PREPARING_STOCK),
            (states.PREPARING_STOCK, states.STOCK_PREPARED),
            (states.STOCK_PREPARED, states.ERP_SALE_SUBMITTED),
            (states.ERP_SALE_SUBMITTED, states.COMPLETED),
        ):
            with self.subTest(transition=f"{current}->{target}"):
                states.validate_transition(current, target)

    def test_the_fiscal_walk(self) -> None:
        states.validate_transition(states.ERP_SALE_SUBMITTED, states.FISCAL_PENDING)
        states.validate_transition(states.FISCAL_PENDING, states.FISCAL_RETRY)
        states.validate_transition(states.FISCAL_RETRY, states.FISCAL_PENDING)
        states.validate_transition(states.FISCAL_PENDING, states.COMPLETED)

    def test_staying_put_is_allowed(self) -> None:
        states.validate_transition(states.RESERVED, states.RESERVED)


class RefusalTests(TestCase):
    def test_a_sale_cannot_be_submitted_before_stock_is_prepared(self) -> None:
        with self.assertRaises(GSFError):
            states.validate_transition(states.RESERVED, states.ERP_SALE_SUBMITTED)

    def test_a_completed_checkout_cannot_be_cancelled(self) -> None:
        with self.assertRaises(GSFError):
            states.validate_transition(states.COMPLETED, states.CANCELLED)

    def test_a_completed_checkout_can_only_go_to_a_return(self) -> None:
        states.validate_transition(states.COMPLETED, states.RETURN_IN_PROGRESS)

    def test_compensated_is_terminal(self) -> None:
        for target in (states.RESERVED, states.COMPLETED, states.COMPENSATING):
            with self.subTest(target=target), self.assertRaises(GSFError):
                states.validate_transition(states.COMPENSATED, target)

    def test_unknown_states_are_rejected(self) -> None:
        with self.assertRaises(GSFError):
            states.validate_transition("PAID", states.COMPLETED)


class OperatorTakeoverTests(TestCase):
    """§35: every uncertain state has to reach a human."""

    def test_uncertain_states_can_reach_manual_review(self) -> None:
        for current in (
            states.PREPARING_STOCK,
            states.STOCK_PREPARED,
            states.ERP_SALE_SUBMITTED,
            states.FISCAL_PENDING,
            states.FISCAL_RETRY,
            states.COMPENSATING,
            states.FAILED,
        ):
            with self.subTest(state=current):
                states.validate_transition(current, states.MANUAL_REVIEW)

    def test_an_operator_can_finish_or_abandon_from_review(self) -> None:
        for target in (states.COMPLETED, states.CANCELLED, states.COMPENSATING):
            with self.subTest(target=target):
                states.validate_transition(states.MANUAL_REVIEW, target)


class ReversibilityTests(TestCase):
    """§14.6 and §23.2: where the line between rollback and compensation falls."""

    def test_nothing_is_posted_before_preparation(self) -> None:
        self.assertFalse(states.needs_compensation(states.RESERVED))
        self.assertTrue(states.is_reversible(states.RESERVED))

    def test_staged_stock_owes_a_compensation(self) -> None:
        self.assertTrue(states.needs_compensation(states.PREPARING_STOCK))
        self.assertTrue(states.needs_compensation(states.STOCK_PREPARED))

    def test_a_submitted_sale_is_past_the_line(self) -> None:
        for status in (
            states.ERP_SALE_SUBMITTED,
            states.FISCAL_PENDING,
            states.FISCAL_RETRY,
            states.COMPLETED,
        ):
            with self.subTest(status=status):
                self.assertFalse(states.is_reversible(status))

    def test_terminal_states(self) -> None:
        self.assertTrue(states.is_terminal(states.COMPLETED))
        self.assertTrue(states.is_terminal(states.COMPENSATED))
        self.assertFalse(states.is_terminal(states.MANUAL_REVIEW))


class ResumeTests(TestCase):
    """What a recovery pass does with a checkout it finds in each state."""

    def test_each_live_state_names_its_next_action(self) -> None:
        for status, step in (
            (states.DRAFT, "reserve"),
            (states.RESERVING, "reserve"),
            (states.RESERVED, "prepare"),
            (states.PREPARING_STOCK, "prepare"),
            (states.STOCK_PREPARED, "sell"),
            (states.ERP_SALE_SUBMITTED, "complete"),
            (states.FISCAL_PENDING, "await_fiscal"),
            (states.COMPENSATING, "compensate"),
        ):
            with self.subTest(status=status):
                self.assertEqual(states.next_step(status), step)

    def test_a_half_finished_step_resumes_at_that_step(self) -> None:
        """RESERVING and PREPARING_STOCK resume where they stopped, not before.

        Both steps are idempotent per line, so repeating them finishes the ones
        that did not land rather than redoing the ones that did.
        """
        self.assertEqual(states.next_step(states.RESERVING), "reserve")
        self.assertEqual(states.next_step(states.PREPARING_STOCK), "prepare")

    def test_terminal_and_human_states_have_no_next_action(self) -> None:
        for status in (
            states.COMPLETED,
            states.CANCELLED,
            states.COMPENSATED,
            states.MANUAL_REVIEW,
            states.FAILED,
            states.EXPIRED,
            states.RETURNED,
        ):
            with self.subTest(status=status):
                self.assertIsNone(states.next_step(status))


class CoverageTests(TestCase):
    def test_every_status_is_in_the_transition_table(self) -> None:
        declared = {
            value
            for name, value in vars(states).items()
            if name.isupper() and isinstance(value, str) and not name.endswith("STATES")
        }
        self.assertEqual(declared, set(states.TRANSITIONS))

    def test_every_target_is_itself_a_known_state(self) -> None:
        for current, targets in states.TRANSITIONS.items():
            for target in targets:
                with self.subTest(transition=f"{current}->{target}"):
                    self.assertIn(target, states.TRANSITIONS)
