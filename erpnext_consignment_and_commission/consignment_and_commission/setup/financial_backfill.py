"""Idempotent upgrade backfill for immutable currency and settlement snapshots."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..services.sale_financials import SaleFinancialSnapshot, convert_sale_financials_to_base


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _currency_precision(frappe: Any) -> int:
    return int(frappe.db.get_single_value("System Settings", "currency_precision") or 2)


def _snapshot(row: Any) -> SaleFinancialSnapshot:
    return SaleFinancialSnapshot(
        relationship_model=row.relationship_model,
        qty=_decimal(row.qty),
        gross_amount=_decimal(row.net_amount),
        commission_rate=_decimal(row.commission_rate),
        commission_amount=_decimal(row.commission_amount),
        partner_unit_rate=_decimal(row.partner_unit_rate),
        partner_amount=_decimal(row.partner_amount),
        retained_amount=_decimal(row.retained_amount),
    )


def _backfill_sale_allocations(frappe: Any, precision: int) -> None:
    if not frappe.db.exists("DocType", "CC Sale Allocation"):
        return
    fields = [
        "name",
        "sales_invoice",
        "relationship_model",
        "sold_qty as qty",
        "net_amount",
        "commission_rate",
        "commission_amount",
        "partner_unit_rate",
        "partner_amount",
        "retained_amount",
        "conversion_rate",
        "base_net_amount",
    ]
    for row in frappe.get_all("CC Sale Allocation", fields=fields):
        if _decimal(row.base_net_amount) > 0:
            continue
        rate = _decimal(row.conversion_rate)
        if rate <= 0 and row.sales_invoice:
            rate = _decimal(frappe.db.get_value("Sales Invoice", row.sales_invoice, "conversion_rate"))
        if rate <= 0:
            raise RuntimeError(f"Cannot backfill CC Sale Allocation {row.name}: conversion rate is missing")
        base = convert_sale_financials_to_base(
            _snapshot(row),
            conversion_rate=rate,
            currency_precision=precision,
        )
        frappe.db.set_value(
            "CC Sale Allocation",
            row.name,
            {
                "conversion_rate": rate,
                "base_net_amount": base.gross_amount,
                "base_commission_amount": base.commission_amount,
                "base_partner_amount": base.partner_amount,
                "base_retained_amount": base.retained_amount,
            },
            update_modified=False,
        )


def _backfill_return_allocations(frappe: Any, precision: int) -> None:
    if not frappe.db.exists("DocType", "CC Sale Return Allocation"):
        return
    fields = [
        "name",
        "sale_allocation",
        "relationship_model",
        "returned_qty as qty",
        "net_amount",
        "commission_amount",
        "partner_amount",
        "retained_amount",
        "base_net_amount",
    ]
    for row in frappe.get_all("CC Sale Return Allocation", fields=fields):
        if _decimal(row.base_net_amount) > 0:
            continue
        sale = frappe.db.get_value(
            "CC Sale Allocation",
            row.sale_allocation,
            ["conversion_rate", "commission_rate", "partner_unit_rate"],
            as_dict=True,
        )
        if not sale or _decimal(sale.conversion_rate) <= 0:
            raise RuntimeError(
                f"Cannot backfill CC Sale Return Allocation {row.name}: sale rate is missing"
            )
        row.commission_rate = sale.commission_rate
        row.partner_unit_rate = sale.partner_unit_rate
        base = convert_sale_financials_to_base(
            _snapshot(row),
            conversion_rate=sale.conversion_rate,
            currency_precision=precision,
        )
        frappe.db.set_value(
            "CC Sale Return Allocation",
            row.name,
            {
                "base_net_amount": base.gross_amount,
                "base_commission_amount": base.commission_amount,
                "base_partner_amount": base.partner_amount,
                "base_retained_amount": base.retained_amount,
            },
            update_modified=False,
        )


def _backfill_settlement_reports(frappe: Any, precision: int) -> None:
    if not frappe.db.exists("DocType", "CC Settlement Report"):
        return
    quantum = Decimal("1").scaleb(-precision)
    for report in frappe.get_all(
        "CC Settlement Report",
        fields=["name", "docstatus", "total_partner_amount", "base_total_partner_amount"],
    ):
        items = frappe.get_all(
            "CC Settlement Report Item",
            filters={"parent": report.name},
            fields=["name", "sale_allocation", "partner_amount", "base_partner_amount"],
            order_by="idx asc",
        )
        base_total = Decimal("0")
        for item in items:
            base_item = _decimal(item.base_partner_amount)
            if base_item <= 0:
                sale = frappe.db.get_value(
                    "CC Sale Allocation",
                    item.sale_allocation,
                    ["partner_amount", "base_partner_amount"],
                    as_dict=True,
                )
                if not sale or _decimal(sale.partner_amount) <= 0:
                    raise RuntimeError(
                        f"Cannot backfill Settlement Report item {item.name}: sale snapshot is missing"
                    )
                base_item = (
                    _decimal(item.partner_amount)
                    * _decimal(sale.base_partner_amount)
                    / _decimal(sale.partner_amount)
                ).quantize(quantum, rounding=ROUND_HALF_UP)
                frappe.db.set_value(
                    "CC Settlement Report Item",
                    item.name,
                    "base_partner_amount",
                    base_item,
                    update_modified=False,
                )
            base_total += base_item
        total = _decimal(report.total_partner_amount)
        values = {
            "base_total_partner_amount": base_total or _decimal(report.base_total_partner_amount),
            "adjusted_amount": 0,
            "base_adjusted_amount": 0,
            "net_partner_amount": total,
            "partner_credit_amount": 0,
        }
        frappe.db.set_value(
            "CC Settlement Report",
            report.name,
            values,
            update_modified=False,
        )
        if report.docstatus == 1:
            from ..integrations.settlements import refresh_settlement_lifecycle

            refresh_settlement_lifecycle(frappe, report.name)


def backfill_financial_snapshots() -> None:
    """Fill only missing upgrade fields and then reconcile submitted report lifecycle."""
    import frappe

    precision = _currency_precision(frappe)
    _backfill_sale_allocations(frappe, precision)
    _backfill_return_allocations(frappe, precision)
    _backfill_settlement_reports(frappe, precision)
    frappe.clear_cache()
