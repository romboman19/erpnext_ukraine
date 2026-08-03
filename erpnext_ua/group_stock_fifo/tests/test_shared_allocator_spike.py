from datetime import datetime
from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.allocation import StockCandidate
from erpnext_ua.consignment_and_commission.services.candidates import (
    CandidateQuery,
    CCStockLotSnapshot,
    candidates_from_cc_stock_lot,
)
from erpnext_ua.group_stock_fifo.spikes.shared_allocator import (
    GroupStockPool,
    GSFLayerSnapshot,
    pool_query,
    three_company_pool,
    total_cost,
)


class SharedAllocatorSpikeTests(TestCase):
    """Gate 0g: the shipped allocator serves the GSF scope with no new rule."""

    def setUp(self) -> None:
        self.pool = three_company_pool()

    def test_global_fifo_crosses_companies_and_ignores_the_seller(self) -> None:
        # §4 forbids seller-first FIFO: FOP C sells, but its own layer is last.
        lines = self.pool.plan(query=pool_query("FOP C", self.pool), qty=Decimal("6"))

        self.assertEqual(
            [(line.owner_company, line.qty) for line in lines],
            [("FOP A", Decimal("2")), ("FOP B", Decimal("3")), ("FOP C", Decimal("1"))],
        )
        self.assertEqual(total_cost(lines), Decimal("6500"))

    def test_slices_owned_by_other_companies_are_flagged_for_reallocation(self) -> None:
        lines = self.pool.plan(query=pool_query("FOP C", self.pool), qty=Decimal("6"))

        reallocated = sum((line.qty for line in lines if line.needs_reallocation), Decimal("0"))
        self.assertEqual(reallocated, Decimal("5"))
        self.assertEqual([line.layer_name for line in lines if not line.needs_reallocation], ["GSF-C"])

    def test_seller_identity_does_not_change_the_selection(self) -> None:
        as_a = self.pool.plan(query=pool_query("FOP A", self.pool), qty=Decimal("4"))
        as_b = self.pool.plan(query=pool_query("FOP B", self.pool), qty=Decimal("4"))

        self.assertEqual([line.layer_name for line in as_a], [line.layer_name for line in as_b])
        self.assertEqual(total_cost(as_a), total_cost(as_b))

    def test_commission_adapter_stays_inert_outside_its_warehouses(self) -> None:
        # Gate 0i at service level: warehouse ownership alone keeps domains apart.
        lines = self.pool.plan(
            query=pool_query("FOP C", self.pool),
            qty=Decimal("6"),
            extra_adapters=[_CCAdapter(_commission_lot())],
        )

        self.assertEqual(len(lines), 3)
        self.assertEqual(total_cost(lines), Decimal("6500"))

    def test_a_wrong_warehouse_binding_would_cross_the_domains(self) -> None:
        # Negative control for the test above: isolation rests entirely on
        # GSF Warehouse Binding, so the registry needs its own guard.
        query = pool_query("FOP C", self.pool)
        widened = CandidateQuery(
            item_code=query.item_code,
            company=query.company,
            location=query.location,
            allowed_warehouses=query.allowed_warehouses | {"Commission - GSF"},
        )

        # The commission lot is the oldest, so it is selected first and the
        # planner cannot resolve its owner. Fails closed, but on a raw KeyError:
        # production needs a controlled domain error here.
        with self.assertRaises(KeyError):
            self.pool.plan(query=widened, qty=Decimal("6"), extra_adapters=[_CCAdapter(_commission_lot())])

    def test_identical_timestamps_stay_deterministic(self) -> None:
        # Gate 0f fallback: GSF never has to trust ERPNext ordering, because the
        # allocator breaks ties on receipt, row index and layer name.
        pool = GroupStockPool(
            [
                _same_second_layer("GSF-Z", "FOP B", receipt="GSF-RECEIPT-2", row_index=1),
                _same_second_layer("GSF-Y", "FOP A", receipt="GSF-RECEIPT-1", row_index=2),
                _same_second_layer("GSF-X", "FOP C", receipt="GSF-RECEIPT-1", row_index=1),
            ]
        )

        order = [line.layer_name for line in pool.plan(query=pool_query("FOP A", pool), qty=Decimal("3"))]

        self.assertEqual(order, ["GSF-X", "GSF-Y", "GSF-Z"])


class _CCAdapter:
    def __init__(self, snapshot: CCStockLotSnapshot) -> None:
        self._snapshot = snapshot

    def load(self, query: CandidateQuery) -> list[StockCandidate]:
        return candidates_from_cc_stock_lot(self._snapshot)


def _commission_lot() -> CCStockLotSnapshot:
    # Older than every GSF layer, so it would win any FIFO run that saw it.
    return CCStockLotSnapshot(
        lot_name="CC-LOT-1",
        item_code="ITEM-1",
        warehouse="Commission - GSF",
        location="Rivne Central",
        source_method="COMMISSION",
        relationship_model="COMMISSION",
        fifo_datetime=datetime(2026, 7, 1, 8, 0),
        receipt_name="CC-RECEIPT-1",
        receipt_row_index=1,
        received_qty=Decimal("10"),
        active_balance=Decimal("10"),
        reserved_qty=Decimal("0"),
        lot_status="OPEN",
        tracking_type="NONE",
    )


def _same_second_layer(name: str, owner: str, *, receipt: str, row_index: int) -> GSFLayerSnapshot:
    return GSFLayerSnapshot(
        layer_name=name,
        item_code="ITEM-1",
        owner_company=owner,
        warehouse=f"Group Pool {owner} - GSF",
        physical_location="Rivne Central",
        fifo_datetime=datetime(2026, 7, 27, 8, 0),
        receipt_name=receipt,
        receipt_row_index=row_index,
        active_balance=Decimal("1"),
        unit_cost=Decimal("1000"),
    )
