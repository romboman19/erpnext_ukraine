"""Unit tests for the Phase 2 layer-registry rules (§9.9–§9.11, §11.3)."""

from unittest import TestCase

from erpnext_ua.group_stock_fifo.services.domain import (
    LAYER_BLOCKED,
    LAYER_CANCELLED,
    LAYER_EXHAUSTED,
    LAYER_OPEN,
    LAYER_PENDING,
    GSFError,
    LayerOrigin,
    MovementFacts,
    balance_identity,
    check_layer_immutability,
    layer_identity,
    validate_layer_transition,
    validate_movement,
    validate_tracking_identity,
)

SITE = "postest.local"


def origin(**overrides):
    values = {
        "company_group": "HUNTER.rv",
        "origin_doctype": "Purchase Receipt",
        "origin_document": "MAT-PRE-2026-00001",
        "origin_row_name": "a1b2c3d4e5",
        "item_code": "TEST-ITEM",
    }
    values.update(overrides)
    return LayerOrigin(**values)


class LayerIdentityTests(TestCase):
    """§11.3: reprocessing the same receipt row must land on the same layer."""

    def test_is_stable_across_calls(self) -> None:
        self.assertEqual(layer_identity(origin(), site_id=SITE), layer_identity(origin(), site_id=SITE))

    def test_is_prefixed_and_bounded(self) -> None:
        name = layer_identity(origin(), site_id=SITE)
        self.assertTrue(name.startswith("GSFL-"))
        self.assertLessEqual(len(name), 140)

    def test_every_coordinate_changes_the_identity(self) -> None:
        base = layer_identity(origin(), site_id=SITE)
        variants = {
            "site": layer_identity(origin(), site_id="other.local"),
            "company_group": layer_identity(origin(company_group="OTHER"), site_id=SITE),
            "origin_doctype": layer_identity(origin(origin_doctype="Purchase Invoice"), site_id=SITE),
            "origin_document": layer_identity(origin(origin_document="MAT-PRE-2026-00002"), site_id=SITE),
            "origin_row_name": layer_identity(origin(origin_row_name="ffffffffff"), site_id=SITE),
            "item_code": layer_identity(origin(item_code="OTHER-ITEM"), site_id=SITE),
            "batch": layer_identity(origin(batch_no="BATCH-1"), site_id=SITE),
            "serials": layer_identity(origin(serial_numbers=("SN-1",)), site_id=SITE),
        }
        for coordinate, name in variants.items():
            with self.subTest(coordinate=coordinate):
                self.assertNotEqual(base, name)
        self.assertEqual(len(set(variants.values())), len(variants))

    def test_serial_order_does_not_change_the_identity(self) -> None:
        """The same physical serials in a different row order are one layer."""
        self.assertEqual(
            layer_identity(origin(serial_numbers=("SN-2", "SN-1")), site_id=SITE),
            layer_identity(origin(serial_numbers=("SN-1", "SN-2")), site_id=SITE),
        )

    def test_coordinates_cannot_be_shifted_between_fields(self) -> None:
        """A separator that cannot appear in a name keeps the tuple unambiguous."""
        self.assertNotEqual(
            layer_identity(origin(origin_document="A", origin_row_name="B"), site_id=SITE),
            layer_identity(origin(origin_document="AB", origin_row_name=""), site_id=SITE),
        )


class LayerTransitionTests(TestCase):
    """§9.9 lifecycle."""

    def test_pending_opens(self) -> None:
        validate_layer_transition(LAYER_PENDING, LAYER_OPEN)

    def test_staying_put_is_allowed(self) -> None:
        validate_layer_transition(LAYER_OPEN, LAYER_OPEN)

    def test_pending_cannot_jump_to_exhausted(self) -> None:
        with self.assertRaises(GSFError):
            validate_layer_transition(LAYER_PENDING, LAYER_EXHAUSTED)

    def test_blocked_can_be_released(self) -> None:
        validate_layer_transition(LAYER_BLOCKED, LAYER_OPEN)

    def test_exhausted_reopens_because_a_reversal_returns_quantity(self) -> None:
        validate_layer_transition(LAYER_EXHAUSTED, LAYER_OPEN)

    def test_cancelled_is_terminal(self) -> None:
        for target in (LAYER_OPEN, LAYER_BLOCKED, LAYER_EXHAUSTED):
            with self.subTest(target=target), self.assertRaises(GSFError):
                validate_layer_transition(LAYER_CANCELLED, target)

    def test_unknown_status_is_rejected(self) -> None:
        with self.assertRaises(GSFError):
            validate_layer_transition("SOMETHING", LAYER_OPEN)


class LayerImmutabilityTests(TestCase):
    """§9.9: identity is settled while PENDING and frozen from OPEN on."""

    before = {"item_code": "A", "origin_document": "MAT-PRE-2026-00001", "layer_status": LAYER_OPEN}

    def test_pending_layers_are_still_editable(self) -> None:
        check_layer_immutability(
            self.before, {**self.before, "item_code": "B"}, previous_status=LAYER_PENDING
        )

    def test_open_layers_reject_an_identity_change(self) -> None:
        with self.assertRaises(GSFError) as caught:
            check_layer_immutability(
                self.before, {**self.before, "item_code": "B"}, previous_status=LAYER_OPEN
            )
        self.assertIn("item_code", str(caught.exception))

    def test_open_layers_still_allow_mutable_fields(self) -> None:
        check_layer_immutability(
            self.before, {**self.before, "blocked_reason": "audit"}, previous_status=LAYER_OPEN
        )

    def test_return_lineage_is_immutable_after_opening(self) -> None:
        before = {
            **self.before,
            "return_origin_layer": "GSFL-SOLD",
            "lineage_root_layer": "GSFL-ROOT",
        }
        with self.assertRaises(GSFError) as caught:
            check_layer_immutability(
                before,
                {**before, "return_origin_layer": "GSFL-OTHER"},
                previous_status=LAYER_OPEN,
            )
        self.assertIn("return_origin_layer", str(caught.exception))


class TrackingIdentityTests(TestCase):
    """§11.2: a tracked receipt must carry the exact identity it claims."""

    def test_untracked_layer_carries_nothing(self) -> None:
        validate_tracking_identity(
            tracking_type="NONE", batch_no=None, serial_numbers=(), qty=3
        )

    def test_untracked_layer_rejects_a_batch(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_tracking_identity(
                tracking_type="NONE", batch_no="B-1", serial_numbers=(), qty=3
            )
        self.assertEqual(caught.exception.code, "BATCH_MISMATCH")

    def test_batch_layer_needs_a_batch(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_tracking_identity(
                tracking_type="BATCH", batch_no=None, serial_numbers=(), qty=3
            )
        self.assertEqual(caught.exception.code, "BATCH_MISMATCH")

    def test_serial_count_must_match_quantity(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_tracking_identity(
                tracking_type="SERIAL", batch_no=None, serial_numbers=("SN-1",), qty=2
            )
        self.assertEqual(caught.exception.code, "SERIAL_AMBIGUOUS")

    def test_serials_may_not_repeat(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_tracking_identity(
                tracking_type="SERIAL", batch_no=None, serial_numbers=("SN-1", "SN-1"), qty=2
            )
        self.assertEqual(caught.exception.code, "SERIAL_AMBIGUOUS")

    def test_a_matching_serial_layer_passes(self) -> None:
        validate_tracking_identity(
            tracking_type="SERIAL", batch_no=None, serial_numbers=("SN-1", "SN-2"), qty=2
        )


class MovementTests(TestCase):
    """§9.11: the audit event has to be self-describing to count as evidence."""

    def movement(self, **overrides):
        values = {"movement_type": "ORIGIN_RECEIPT", "qty": 5, "idempotency_key": "k-1"}
        values.update(overrides)
        return MovementFacts(**values)

    def test_a_plain_receipt_movement_passes(self) -> None:
        validate_movement(self.movement())

    def test_unknown_type_is_rejected(self) -> None:
        with self.assertRaises(GSFError):
            validate_movement(self.movement(movement_type="TELEPORT"))

    def test_movement_needs_an_idempotency_key(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_movement(self.movement(idempotency_key=""))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_zero_quantity_is_not_an_event(self) -> None:
        with self.assertRaises(GSFError):
            validate_movement(self.movement(qty=0))

    def test_a_reversal_must_name_what_it_undoes(self) -> None:
        with self.assertRaises(GSFError):
            validate_movement(self.movement(is_reversal=True))

    def test_only_a_reversal_may_name_one(self) -> None:
        with self.assertRaises(GSFError):
            validate_movement(self.movement(reversal_of="GSFLM-0001"))

    def test_a_well_formed_reversal_passes(self) -> None:
        validate_movement(
            self.movement(movement_type="REVERSAL", is_reversal=True, reversal_of="GSFLM-0001")
        )

    def test_an_opposite_leg_may_carry_the_reversal_flag(self) -> None:
        validate_movement(
            self.movement(
                movement_type="INTERCOMPANY_RECEIPT", is_reversal=True, reversal_of="GSFLM-0001"
            )
        )


class BalanceIdentityTests(TestCase):
    """§9.10: one row per layer/company/warehouse position."""

    def test_is_stable(self) -> None:
        first = balance_identity(stock_layer="GSFL-1", company="FOP A", warehouse="Pool - A")
        second = balance_identity(stock_layer="GSFL-1", company="FOP A", warehouse="Pool - A")
        self.assertEqual(first, second)

    def test_each_coordinate_separates_positions(self) -> None:
        base = balance_identity(stock_layer="GSFL-1", company="FOP A", warehouse="Pool - A")
        others = {
            balance_identity(stock_layer="GSFL-2", company="FOP A", warehouse="Pool - A"),
            balance_identity(stock_layer="GSFL-1", company="FOP B", warehouse="Pool - A"),
            balance_identity(stock_layer="GSFL-1", company="FOP A", warehouse="Stage - A"),
        }
        self.assertNotIn(base, others)
        self.assertEqual(len(others), 3)

    def test_fits_a_frappe_name(self) -> None:
        name = balance_identity(
            stock_layer="GSFL-" + "f" * 32, company="F" * 100, warehouse="W" * 100
        )
        self.assertLessEqual(len(name), 140)
