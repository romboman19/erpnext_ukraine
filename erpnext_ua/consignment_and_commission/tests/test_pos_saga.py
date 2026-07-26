from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.pos_saga import (
    CartLine,
    PaymentTender,
    POSSagaError,
    ReturnSource,
    RouteProgress,
    allocate_payment_plan,
    pending_invoice_actions,
    plan_compensation,
    plan_return,
    split_checkout,
)


def _line(
    row_id: str,
    model: str,
    legal_entity: str,
    *,
    rate: str = "100",
) -> CartLine:
    return CartLine(
        row_id=row_id,
        item_code="ITEM-1",
        qty=Decimal("1"),
        rate=Decimal(rate),
        company="POS Test Ukraine",
        legal_entity=legal_entity,
        relationship_model=model,
        warehouse=f"{model}-WAREHOUSE",
        lot_name=f"{model}-LOT",
    )


class POSSagaTests(TestCase):
    def test_fiscal_cart_splits_by_legal_entity_and_commission_policy(self) -> None:
        groups = split_checkout(
            "POS-1",
            [
                _line("OWN", "OWN", "FOP-A"),
                _line("COM", "COMMISSION", "FOP-A"),
                _line("CON", "CONSIGNMENT", "FOP-B"),
            ],
            fiscal_checkout=True,
        )

        self.assertEqual(
            [(group.key.legal_entity, group.key.fiscal_route) for group in groups],
            [("FOP-A", "FISCAL"), ("FOP-A", "NON_FISCAL"), ("FOP-B", "FISCAL")],
        )
        self.assertEqual([group.print_kind for group in groups], [
            "FISCAL_RECEIPT",
            "NON_FISCAL_GOODS_RECEIPT",
            "FISCAL_RECEIPT",
        ])

    def test_non_fiscal_cart_can_merge_relationship_models_within_one_entity(self) -> None:
        groups = split_checkout(
            "POS-2",
            [_line("OWN", "OWN", "FOP-A"), _line("COM", "COMMISSION", "FOP-A")],
            fiscal_checkout=False,
        )

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].key.fiscal_route, "NON_FISCAL")

    def test_one_payment_plan_is_allocated_across_groups(self) -> None:
        groups = split_checkout(
            "POS-3",
            [
                _line("OWN", "OWN", "FOP-A"),
                _line("COM", "COMMISSION", "FOP-A"),
                _line("CON", "CONSIGNMENT", "FOP-B"),
            ],
            fiscal_checkout=True,
        )
        allocations = allocate_payment_plan(
            groups,
            [
                PaymentTender("CASH", "Cash", Decimal("180")),
                PaymentTender("CARD", "Credit Card", Decimal("120")),
            ],
        )

        self.assertEqual(sum((row.amount for row in allocations), Decimal("0")), Decimal("300"))
        self.assertEqual(
            [row.amount for row in allocations],
            [Decimal("100"), Decimal("80"), Decimal("20"), Decimal("100")],
        )

    def test_retry_only_creates_missing_route_documents(self) -> None:
        groups = split_checkout(
            "POS-4",
            [_line("OWN", "OWN", "FOP-A"), _line("COM", "COMMISSION", "FOP-A")],
            fiscal_checkout=True,
        )
        progress = [RouteProgress(groups[0].group_id, "SUBMITTED", "SINV-1")]

        actions = pending_invoice_actions(groups, progress)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].action, "CREATE_SALES_INVOICE")
        self.assertEqual(actions[0].group_id, groups[1].group_id)

    def test_compensation_reverses_submitted_documents_then_releases_stock(self) -> None:
        actions = plan_compensation(
            [
                RouteProgress("A", "SUBMITTED", "SINV-A"),
                RouteProgress("B", "SUBMITTED", "SINV-B"),
            ]
        )

        self.assertEqual(
            [(action.action, action.document_name) for action in actions],
            [
                ("CANCEL_SALES_INVOICE", "SINV-B"),
                ("CANCEL_SALES_INVOICE", "SINV-A"),
                ("RELEASE_RESERVATIONS", None),
            ],
        )

    def test_return_restores_original_lot_and_ownership(self) -> None:
        restoration = plan_return(
            [
                ReturnSource(
                    allocation_id="ALLOC-1",
                    original_row_id="ROW-1",
                    lot_name="COMMISSION-LOT",
                    warehouse="COMMISSION-WAREHOUSE",
                    relationship_model="COMMISSION",
                    sold_qty=Decimal("2"),
                )
            ],
            {"ALLOC-1": Decimal("1")},
        )[0]

        self.assertEqual(restoration.lot_name, "COMMISSION-LOT")
        self.assertEqual(restoration.warehouse, "COMMISSION-WAREHOUSE")
        self.assertEqual(restoration.relationship_model, "COMMISSION")
        self.assertEqual(restoration.qty, Decimal("1"))

    def test_return_cannot_exceed_unreturned_quantity(self) -> None:
        with self.assertRaises(POSSagaError):
            plan_return(
                [
                    ReturnSource(
                        allocation_id="ALLOC-1",
                        original_row_id="ROW-1",
                        lot_name="LOT",
                        warehouse="WAREHOUSE",
                        relationship_model="OWN",
                        sold_qty=Decimal("1"),
                        returned_qty=Decimal("1"),
                    )
                ],
                {"ALLOC-1": Decimal("1")},
            )
