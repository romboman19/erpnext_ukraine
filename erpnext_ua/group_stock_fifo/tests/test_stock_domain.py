"""Domain-neutral FIFO and legal route tests."""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.services.allocation import StockCandidate
from erpnext_ua.group_stock_fifo.services.stock_domain import (
    DomainCandidate,
    StockDomainQuery,
    plan_domain_fifo,
)

BASE = datetime(2026, 8, 1, 8)


def _entry(
    sequence: int,
    *,
    method: str,
    relationship: str,
    company: str,
    provider: str = "CC",
) -> DomainCandidate:
    lot = f"LOT-{sequence}"
    warehouse = f"Warehouse-{sequence}"
    return DomainCandidate(
        provider_id=provider,
        candidate=StockCandidate(
            lot_name=lot,
            item_code="PHONE",
            warehouse=warehouse,
            location="Rivne",
            source_method=method,
            relationship_model=relationship,
            fifo_datetime=BASE + timedelta(hours=sequence),
            receipt_name=f"RECEIPT-{sequence}",
            receipt_row_index=1,
            available_qty=Decimal("1"),
        ),
        seller_company=company,
        provider_location=f"Rivne-{company}",
        legal_entity_type="Company",
        legal_entity_name=company,
    )


class StockDomainPlanningTests(TestCase):
    def test_four_methods_share_one_physical_fifo(self) -> None:
        candidates = [
            _entry(1, method="BUYOUT", relationship="OWN", company="FOP A"),
            _entry(2, method="BUYOUT", relationship="OWN", company="FOP B"),
            _entry(3, method="CONSIGNMENT", relationship="CONSIGNMENT", company="FOP A"),
            _entry(4, method="COMMISSION", relationship="COMMISSION", company="FOP B"),
        ]
        query = StockDomainQuery(
            company_group="GROUP",
            physical_location="Rivne",
            seller_company="FOP A",
            item_code="PHONE",
            qty=Decimal("4"),
        )

        plan = plan_domain_fifo(query, candidates)

        self.assertEqual(
            [(row.allocation.source_method, row.seller_company) for row in plan],
            [
                ("BUYOUT", "FOP A"),
                ("BUYOUT", "FOP B"),
                ("CONSIGNMENT", "FOP A"),
                ("COMMISSION", "FOP B"),
            ],
        )

    def test_older_external_stock_wins_over_gsf_stock(self) -> None:
        candidates = [
            _entry(1, method="COMMISSION", relationship="COMMISSION", company="FOP B"),
            _entry(
                2,
                method="GSF_LAYER",
                relationship="OWN",
                company="FOP A",
                provider="GSF",
            ),
        ]
        query = StockDomainQuery(
            company_group="GROUP",
            physical_location="Rivne",
            seller_company="FOP A",
            item_code="PHONE",
            qty=Decimal("1"),
        )

        plan = plan_domain_fifo(query, candidates)

        self.assertEqual(plan[0].provider_id, "CC")
        self.assertEqual(plan[0].seller_company, "FOP B")
        self.assertEqual(plan[0].fiscal_route, "NON_FISCAL")
