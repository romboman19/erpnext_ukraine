from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.reservation import (
    ReservationError,
    ReservationRequest,
    reservation_fingerprint,
    validate_allocation_transition,
    validate_reservation_request,
)


class ReservationPolicyTests(TestCase):
    def test_fingerprint_is_stable_across_warehouse_order(self) -> None:
        left = self.request(allowed_warehouses=frozenset({"OWN", "COMMISSION"}))
        right = self.request(allowed_warehouses=frozenset({"COMMISSION", "OWN"}))
        self.assertEqual(reservation_fingerprint(left), reservation_fingerprint(right))

    def test_fingerprint_changes_with_business_payload(self) -> None:
        self.assertNotEqual(
            reservation_fingerprint(self.request()),
            reservation_fingerprint(self.request(qty=Decimal("2"))),
        )

    def test_request_requires_positive_quantity_and_exact_serial_unit(self) -> None:
        with self.assertRaisesRegex(ReservationError, "greater than zero"):
            validate_reservation_request(self.request(qty=Decimal("0")))
        with self.assertRaisesRegex(ReservationError, "must equal one"):
            validate_reservation_request(self.request(qty=Decimal("2"), serial_no="SER-1"))

    def test_idempotency_key_is_bounded_and_normalized(self) -> None:
        with self.assertRaisesRegex(ReservationError, "Idempotency key"):
            validate_reservation_request(self.request(idempotency_key=" key "))
        with self.assertRaisesRegex(ReservationError, "Idempotency key"):
            validate_reservation_request(self.request(idempotency_key="x" * 141))

    def test_only_reserved_allocation_can_reach_terminal_state(self) -> None:
        validate_allocation_transition("PENDING", "RESERVED")
        for status in ("CONSUMED", "RELEASED", "EXPIRED"):
            validate_allocation_transition("RESERVED", status)
        with self.assertRaisesRegex(ReservationError, "cannot transition"):
            validate_allocation_transition("RELEASED", "RESERVED")

    @staticmethod
    def request(**overrides: object) -> ReservationRequest:
        values = {
            "idempotency_key": "POS-1:ROW-1",
            "item_code": "ITEM-1",
            "company": "Company",
            "location": "Location",
            "qty": Decimal("1"),
            "allowed_warehouses": frozenset({"OWN"}),
        }
        values.update(overrides)
        return ReservationRequest(**values)  # type: ignore[arg-type]
