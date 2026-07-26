from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.pos_checkout import (
    POSCheckoutError,
    POSCheckoutRequest,
    POSRouteLine,
    POSRouteRequest,
    pos_checkout_fingerprint,
)
from erpnext_ua.consignment_and_commission.services.pos_saga import (
    PaymentTender,
)


def _request() -> POSCheckoutRequest:
    return POSCheckoutRequest(
        idempotency_key="checkout-1",
        external_order_doctype="POS Order",
        external_order_name="POS-1",
        customer="Customer",
        posting_date=date(2026, 7, 14),
        currency="UAH",
        conversion_rate=Decimal("1"),
        fiscal_checkout=True,
        routes=(
            POSRouteRequest(
                group_id="fiscal",
                company="Company",
                location="Store",
                legal_entity_type="Company",
                legal_entity_name="Company",
                fiscal_route="FISCAL",
                lines=(POSRouteLine("allocation-1", Decimal("100"), "row-1"),),
            ),
            POSRouteRequest(
                group_id="non-fiscal",
                company="Company",
                location="Store",
                legal_entity_type="Company",
                legal_entity_name="Company",
                fiscal_route="NON_FISCAL",
                lines=(POSRouteLine("allocation-2", Decimal("50"), "row-2"),),
            ),
        ),
        tenders=(PaymentTender("cash", "Cash", Decimal("150")),),
    )


class POSCheckoutContractTests(TestCase):
    def test_fingerprint_is_order_independent_and_payload_sensitive(self) -> None:
        request = _request()
        reordered = replace(request, routes=tuple(reversed(request.routes)))
        self.assertEqual(pos_checkout_fingerprint(request), pos_checkout_fingerprint(reordered))
        changed = replace(request, conversion_rate=Decimal("1.01"))
        self.assertNotEqual(pos_checkout_fingerprint(request), pos_checkout_fingerprint(changed))

    def test_duplicate_allocation_and_invalid_route_fail_closed(self) -> None:
        request = _request()
        duplicate = replace(
            request,
            routes=(
                request.routes[0],
                replace(request.routes[1], lines=request.routes[0].lines),
            ),
        )
        with self.assertRaisesRegex(POSCheckoutError, "reuse an allocation"):
            pos_checkout_fingerprint(duplicate)
