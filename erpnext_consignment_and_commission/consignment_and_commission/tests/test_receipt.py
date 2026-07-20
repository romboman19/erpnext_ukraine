from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.receipt import (
    ContractReceiptPolicy,
    ReceiptLinePolicy,
    ReceiptValidationError,
    StockLotPolicy,
    receipt_warehouse,
    validate_contract_for_receipt,
    validate_receipt_line,
    validate_stock_lot,
)


class ReceiptPolicyTests(TestCase):
    def test_active_contract_accepts_date_inside_validity(self) -> None:
        validate_contract_for_receipt(
            ContractReceiptPolicy(
                status="ACTIVE",
                relationship_model="COMMISSION",
                valid_from=date(2026, 7, 1),
                valid_to=date(2026, 7, 31),
                posting_date=date(2026, 7, 13),
            )
        )

    def test_receipt_rejects_non_active_or_expired_contract(self) -> None:
        with self.assertRaisesRegex(ReceiptValidationError, "Active"):
            validate_contract_for_receipt(self._contract(status="DRAFT"))
        with self.assertRaisesRegex(ReceiptValidationError, "end date"):
            validate_contract_for_receipt(self._contract(posting_date=date(2026, 8, 1)))

    def test_plain_stock_uom_line_returns_stock_quantity(self) -> None:
        self.assertEqual(validate_receipt_line(self._line()), Decimal("3"))

    def test_receipt_line_requires_positive_quantity(self) -> None:
        with self.assertRaisesRegex(ReceiptValidationError, "greater than zero"):
            validate_receipt_line(self._line(qty=Decimal("0")))

    def test_first_slice_rejects_alternate_uom(self) -> None:
        with self.assertRaisesRegex(ReceiptValidationError, "stock UOM"):
            validate_receipt_line(self._line(uom="Box", conversion_factor=Decimal("10")))

    def test_stock_uom_quantity_validation_is_shared_by_tracked_items(self) -> None:
        self.assertEqual(
            validate_receipt_line(self._line(has_serial_no=True)),
            Decimal("3"),
        )
        self.assertEqual(
            validate_receipt_line(self._line(has_batch_no=True)),
            Decimal("3"),
        )

    def test_relationship_model_selects_only_its_technical_warehouse(self) -> None:
        self.assertEqual(
            receipt_warehouse(
                "COMMISSION",
                commission_warehouse="Commission - CCI",
                consignment_warehouse="Consignment - CCI",
            ),
            "Commission - CCI",
        )
        self.assertEqual(
            receipt_warehouse(
                "CONSIGNMENT",
                commission_warehouse="Commission - CCI",
                consignment_warehouse="Consignment - CCI",
            ),
            "Consignment - CCI",
        )

    def test_stock_lot_reservation_cannot_exceed_received_quantity(self) -> None:
        with self.assertRaisesRegex(ReceiptValidationError, "cannot exceed"):
            validate_stock_lot(
                StockLotPolicy("COMMISSION", "COMMISSION", Decimal("2"), Decimal("3"), "OPEN")
            )

    def test_stock_lot_accepts_owned_sources_and_rejects_source_model_mismatch(self) -> None:
        validate_stock_lot(
            StockLotPolicy("OWN", "BUYOUT", Decimal("2"), Decimal("0"), "OPEN")
        )
        with self.assertRaisesRegex(ReceiptValidationError, "requires relationship model"):
            validate_stock_lot(
                StockLotPolicy("OWN", "COMMISSION", Decimal("2"), Decimal("0"), "OPEN")
            )

    def test_stock_lot_rejects_unknown_status(self) -> None:
        with self.assertRaisesRegex(ReceiptValidationError, "Unsupported"):
            validate_stock_lot(
                StockLotPolicy(
                    "COMMISSION", "COMMISSION", Decimal("2"), Decimal("0"), "UNKNOWN"
                )
            )

    @staticmethod
    def _contract(**overrides) -> ContractReceiptPolicy:
        values = {
            "status": "ACTIVE",
            "relationship_model": "COMMISSION",
            "valid_from": date(2026, 7, 1),
            "valid_to": date(2026, 7, 31),
            "posting_date": date(2026, 7, 13),
        }
        values.update(overrides)
        return ContractReceiptPolicy(**values)

    @staticmethod
    def _line(**overrides) -> ReceiptLinePolicy:
        values = {
            "item_code": "ITEM-1",
            "qty": Decimal("3"),
            "uom": "Nos",
            "stock_uom": "Nos",
            "conversion_factor": Decimal("1"),
            "is_stock_item": True,
            "disabled": False,
            "has_serial_no": False,
            "has_batch_no": False,
        }
        values.update(overrides)
        return ReceiptLinePolicy(**values)
