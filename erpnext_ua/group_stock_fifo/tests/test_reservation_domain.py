"""Unit tests for the Phase 3 reservation rules (§9.12, §13.3, §13.4)."""

from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.allocation import (
    SOURCE_METHOD_RELATIONSHIP_MODEL,
)
from erpnext_ua.group_stock_fifo.services.domain import GSFError
from erpnext_ua.group_stock_fifo.services.reservation import (
    ALLOCATION_CONSUMED,
    ALLOCATION_EXPIRED,
    ALLOCATION_FAILED,
    ALLOCATION_PENDING,
    ALLOCATION_PREPARED,
    ALLOCATION_PREPARING,
    ALLOCATION_RELEASED,
    ALLOCATION_RESERVED,
    ALLOCATION_REVERSED,
    GSF_RELATIONSHIP_MODEL,
    GSF_SOURCE_METHOD,
    ReservationRequest,
    allocation_identity,
    allocation_retry_delay,
    needs_compensation,
    reservation_fingerprint,
    scope_lock_identity,
    validate_allocation_transition,
    validate_reservation_request,
)


def request(**overrides):
    values = {
        "idempotency_key": "checkout-1:row-1",
        "company_group": "HUNTER.rv",
        "physical_location": "Rivne Central",
        "seller_company": "FOP C",
        "item_code": "TEST-ITEM",
        "qty": Decimal("6"),
        "allowed_warehouses": frozenset({"Pool A", "Pool B", "Pool C"}),
    }
    values.update(overrides)
    return ReservationRequest(**values)


class SourceMethodTests(TestCase):
    """Gate 0g's one predicted change to the shared allocator."""

    def test_gsf_layer_maps_to_own_stock(self) -> None:
        self.assertEqual(SOURCE_METHOD_RELATIONSHIP_MODEL[GSF_SOURCE_METHOD], GSF_RELATIONSHIP_MODEL)

    def test_the_commission_methods_are_untouched(self) -> None:
        for method, model in (
            ("BUYOUT", "OWN"),
            ("DEFERRED_PURCHASE", "OWN"),
            ("COMMISSION", "COMMISSION"),
            ("CONSIGNMENT", "CONSIGNMENT"),
        ):
            with self.subTest(method=method):
                self.assertEqual(SOURCE_METHOD_RELATIONSHIP_MODEL[method], model)


class RequestValidationTests(TestCase):
    def test_a_plain_request_passes(self) -> None:
        validate_reservation_request(request())

    def test_key_is_required(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_reservation_request(request(idempotency_key=""))
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_key_may_not_carry_edge_whitespace(self) -> None:
        with self.assertRaises(GSFError):
            validate_reservation_request(request(idempotency_key=" checkout-1 "))

    def test_scope_coordinates_are_required(self) -> None:
        for field in ("company_group", "physical_location", "seller_company", "item_code"):
            with self.subTest(field=field), self.assertRaises(GSFError):
                validate_reservation_request(request(**{field: ""}))

    def test_quantity_must_be_positive(self) -> None:
        with self.assertRaises(GSFError):
            validate_reservation_request(request(qty=Decimal("0")))

    def test_at_least_one_warehouse(self) -> None:
        with self.assertRaises(GSFError):
            validate_reservation_request(request(allowed_warehouses=frozenset()))

    def test_serial_request_is_one_unit(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_reservation_request(request(serial_no="SN-1", qty=Decimal("2")))
        self.assertEqual(caught.exception.code, "SERIAL_AMBIGUOUS")

    def test_serial_and_batch_are_exclusive(self) -> None:
        with self.assertRaises(GSFError):
            validate_reservation_request(
                request(serial_no="SN-1", batch_no="B-1", qty=Decimal("1"))
            )


class FingerprintTests(TestCase):
    """§13.4 — every coordinate that changes what gets reserved is covered."""

    def test_is_stable(self) -> None:
        self.assertEqual(reservation_fingerprint(request()), reservation_fingerprint(request()))

    def test_warehouse_set_order_does_not_matter(self) -> None:
        self.assertEqual(
            reservation_fingerprint(request(allowed_warehouses=frozenset({"Pool C", "Pool A", "Pool B"}))),
            reservation_fingerprint(request()),
        )

    def test_quantity_is_compared_by_value_not_spelling(self) -> None:
        self.assertEqual(
            reservation_fingerprint(request(qty=Decimal("6.00"))),
            reservation_fingerprint(request(qty=Decimal("6"))),
        )

    def test_every_coordinate_moves_the_fingerprint(self) -> None:
        base = reservation_fingerprint(request())
        variants = {
            "company_group": request(company_group="OTHER"),
            "physical_location": request(physical_location="Lutsk"),
            "seller_company": request(seller_company="FOP A"),
            "item_code": request(item_code="OTHER-ITEM"),
            "qty": request(qty=Decimal("7")),
            "warehouses": request(allowed_warehouses=frozenset({"Pool A"})),
            "batch_no": request(batch_no="B-1"),
            "item_policy": request(item_policy="NO_REALLOCATION"),
            "external_row_id": request(external_row_id="ext-9"),
            "posting_date": request(posting_date="2026-07-30"),
            "checkout": request(checkout="CHK-1"),
        }
        fingerprints = {name: reservation_fingerprint(value) for name, value in variants.items()}
        for name, fingerprint in fingerprints.items():
            with self.subTest(coordinate=name):
                self.assertNotEqual(base, fingerprint)
        self.assertEqual(len(set(fingerprints.values())), len(fingerprints))


class RetryDelayTests(TestCase):
    def test_delay_is_deterministic_and_key_specific(self) -> None:
        first = allocation_retry_delay("checkout-1:row-1", 1)
        self.assertEqual(first, allocation_retry_delay("checkout-1:row-1", 1))
        self.assertNotEqual(first, allocation_retry_delay("checkout-2:row-1", 1))

    def test_delay_is_positive_and_bounded(self) -> None:
        delays = [allocation_retry_delay("checkout-1:row-1", attempt) for attempt in range(1, 20)]
        self.assertTrue(all(0 < delay <= 0.5 for delay in delays))
        self.assertGreater(delays[3], delays[0])


class IdentityTests(TestCase):
    def test_allocation_name_covers_key_and_payload(self) -> None:
        key = "checkout-1:row-1"
        first = allocation_identity(idempotency_key=key, fingerprint=reservation_fingerprint(request()))
        same = allocation_identity(idempotency_key=key, fingerprint=reservation_fingerprint(request()))
        other = allocation_identity(
            idempotency_key=key, fingerprint=reservation_fingerprint(request(qty=Decimal("7")))
        )
        self.assertEqual(first, same)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("GSFA-"))

    def test_scope_lock_is_one_row_per_fifo_scope(self) -> None:
        base = scope_lock_identity(
            company_group="HUNTER.rv", physical_location="Rivne Central", item_code="ITEM-1"
        )
        self.assertEqual(
            base,
            scope_lock_identity(
                company_group="HUNTER.rv", physical_location="Rivne Central", item_code="ITEM-1"
            ),
        )
        others = {
            scope_lock_identity(company_group="OTHER", physical_location="Rivne Central", item_code="ITEM-1"),
            scope_lock_identity(company_group="HUNTER.rv", physical_location="Lutsk", item_code="ITEM-1"),
            scope_lock_identity(company_group="HUNTER.rv", physical_location="Rivne Central", item_code="ITEM-2"),
        }
        self.assertNotIn(base, others)
        self.assertEqual(len(others), 3)


class TransitionTests(TestCase):
    """§9.12 lifecycle."""

    def test_the_full_staging_ladder(self) -> None:
        for current, target in (
            (ALLOCATION_PENDING, ALLOCATION_RESERVED),
            (ALLOCATION_RESERVED, ALLOCATION_PREPARING),
            (ALLOCATION_PREPARING, ALLOCATION_PREPARED),
            (ALLOCATION_PREPARED, ALLOCATION_CONSUMED),
            (ALLOCATION_CONSUMED, ALLOCATION_REVERSED),
        ):
            with self.subTest(transition=f"{current}->{target}"):
                validate_allocation_transition(current, target)

    def test_a_sale_needing_no_staging_may_consume_directly(self) -> None:
        validate_allocation_transition(ALLOCATION_RESERVED, ALLOCATION_CONSUMED)

    def test_pending_cannot_skip_to_consumed(self) -> None:
        with self.assertRaises(GSFError) as caught:
            validate_allocation_transition(ALLOCATION_PENDING, ALLOCATION_CONSUMED)
        self.assertEqual(caught.exception.code, "ALLOCATION_CONFLICT")

    def test_released_is_terminal(self) -> None:
        for target in (ALLOCATION_RESERVED, ALLOCATION_CONSUMED, ALLOCATION_PREPARING):
            with self.subTest(target=target), self.assertRaises(GSFError):
                validate_allocation_transition(ALLOCATION_RELEASED, target)

    def test_expired_cannot_be_revived(self) -> None:
        with self.assertRaises(GSFError):
            validate_allocation_transition(ALLOCATION_EXPIRED, ALLOCATION_CONSUMED)

    def test_failed_is_terminal(self) -> None:
        with self.assertRaises(GSFError):
            validate_allocation_transition(ALLOCATION_FAILED, ALLOCATION_RESERVED)

    def test_unknown_status_is_rejected(self) -> None:
        with self.assertRaises(GSFError):
            validate_allocation_transition("HOLD", ALLOCATION_RESERVED)


class CompensationTests(TestCase):
    """§13.3: expiry after staging began is not a plain release."""

    def test_reserved_only_needs_a_release(self) -> None:
        self.assertFalse(needs_compensation(ALLOCATION_RESERVED))

    def test_staged_allocations_need_compensation(self) -> None:
        self.assertTrue(needs_compensation(ALLOCATION_PREPARING))
        self.assertTrue(needs_compensation(ALLOCATION_PREPARED))
