"""Clean-site multi-FOP Batch sale and partial return acceptance."""

from __future__ import annotations

import uuid
from decimal import Decimal

import frappe

from erpnext_ua.group_stock_fifo.setup.layer_dimension import (
    LAYER_FIELD,
    RETURN_ORIGIN_LAYER_FIELD,
)
from erpnext_ua.ua_pos.api import _post_sales_invoice

from .phase_3_fixture import GROUP, assert_site, drain_reposts, pool_name
from .phase_3_fixture import build as build_base
from .phase_6_serial_returns import (
    _ensure_base,
    _ensure_route_warehouses,
    _group_companies,
    _row_value,
)
from .phase_6_serial_returns import _ensure_pos as _ensure_tracked_pos

ITEM = "GSF-P8-BATCH"
BATCHES = (
    ("GSF-P8-BATCH-A", 0, Decimal("1000"), Decimal("2"), "2026-05-01 09:00:00"),
    ("GSF-P8-BATCH-B", 1, Decimal("900"), Decimal("3"), "2026-05-02 09:00:00"),
)


def run() -> dict:
    assert_site()
    if not frappe.db.exists("GSF Company Group", GROUP):
        build_base("BUILD_GSF_PHASE_3")
    firms = _group_companies()
    location = frappe.db.get_value(
        "GSF Physical Location", {"company_group": GROUP}, "name"
    )
    _ensure_base(firms, location)
    _ensure_item()
    for company in firms[:2]:
        _ensure_route_warehouses(company, location)
    receipts = _ensure_batch_receipts(firms)
    drain_reposts()
    frappe.db.commit()

    desk_a, shift_a, employee_a, mode_a = _ensure_tracked_pos(firms[0])
    desk_b, shift_b, employee_b, mode_b = _ensure_tracked_pos(firms[1])
    first_order = _make_order(
        desk_a,
        shift_a,
        employee_a,
        mode_a,
        batch_no="GSF-P8-BATCH-B",
        qty=Decimal("2"),
    )
    first_invoice = _post(first_order, desk_a)
    _assert_batch_sale(
        first_invoice,
        batch_no="GSF-P8-BATCH-B",
        qty=Decimal("2"),
        cost=Decimal("1800"),
        seller=firms[0],
    )

    second_order = _make_order(
        desk_b,
        shift_b,
        employee_b,
        mode_b,
        batch_no="GSF-P8-BATCH-A",
        qty=Decimal("1"),
    )
    second_invoice = _post(second_order, desk_b)
    _assert_batch_sale(
        second_invoice,
        batch_no="GSF-P8-BATCH-A",
        qty=Decimal("1"),
        cost=Decimal("1000"),
        seller=firms[1],
    )

    return_order = _make_order(
        desk_a,
        shift_a,
        employee_a,
        mode_a,
        batch_no="GSF-P8-BATCH-B",
        qty=Decimal("1"),
        original=first_order,
    )
    credit_note = _post(return_order, desk_a)
    _assert_batch_return(
        credit_note,
        original=first_invoice,
        batch_no="GSF-P8-BATCH-B",
        qty=Decimal("1"),
        cost=Decimal("900"),
        seller=firms[0],
    )
    frappe.db.commit()
    return {
        "receipts": receipts,
        "sales": [
            _sale_evidence(first_invoice),
            _sale_evidence(second_invoice),
        ],
        "return": _sale_evidence(credit_note),
    }


def _ensure_item() -> None:
    if frappe.db.exists("Item", ITEM):
        item = frappe.get_cached_doc("Item", ITEM)
        if not item.has_batch_no or item.has_serial_no:
            raise RuntimeError(f"Existing Item {ITEM} has incompatible tracking settings")
    else:
        frappe.get_doc(
            {
                "doctype": "Item",
                "item_code": ITEM,
                "item_name": "GSF Phase 8 Batch Item",
                "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
                "stock_uom": "Nos",
                "is_stock_item": 1,
                "valuation_method": "FIFO",
                "has_batch_no": 1,
                "create_new_batch": 1,
                "batch_number_series": "GSF-P8-BATCH-.#####",
            }
        ).insert(ignore_permissions=True)
    for batch_no, _company_index, _rate, _qty, _posting in BATCHES:
        if not frappe.db.exists("Batch", batch_no):
            frappe.get_doc(
                {"doctype": "Batch", "batch_id": batch_no, "item": ITEM}
            ).insert(ignore_permissions=True)


def _ensure_batch_receipts(firms: list[str]) -> list[str]:
    from erpnext_ua.group_stock_fifo.spikes.stock_setup import ensure_clearing_account

    receipts = []
    for batch_no, company_index, rate, qty, posting in BATCHES:
        company = firms[company_index]
        entry = frappe.get_doc(
            {
                "doctype": "Stock Entry",
                "stock_entry_type": "Material Receipt",
                "purpose": "Material Receipt",
                "company": company,
                "set_posting_time": 1,
                "posting_date": posting.split()[0],
                "posting_time": posting.split()[1],
                "gsf_managed": 1,
                "remarks": f"GSF-P8-{batch_no}",
                "items": [
                    {
                        "item_code": ITEM,
                        "qty": qty,
                        "t_warehouse": pool_name(company),
                        "basic_rate": rate,
                        "set_basic_rate_manually": 1,
                        "expense_account": ensure_clearing_account(frappe, company),
                        "use_serial_batch_fields": 1,
                        "batch_no": batch_no,
                    }
                ],
            }
        )
        entry.insert(ignore_permissions=True)
        entry.submit()
        receipts.append(entry.name)
        layer = frappe.get_doc("GSF Stock Layer", entry.items[0].get("to_gsf_stock_layer"))
        assert layer.batch_no == batch_no
        assert Decimal(str(layer.original_received_qty)) == qty
    return receipts


def _make_order(
    desk,
    shift,
    employee: str,
    mode: str,
    *,
    batch_no: str,
    qty: Decimal,
    original=None,
):
    is_return = original is not None
    order = frappe.get_doc(
        {
            "doctype": "POS Order",
            "cash_desk": desk.name,
            "operational_shift": shift.name,
            "employee": employee,
            "customer": desk.default_customer,
            "order_type": "Return" if is_return else "Sale",
            "return_against": original.name if original else None,
            "fiscal_mode": "Non Fiscal",
            "status": "Paid",
            "lookup_token": str(uuid.uuid4()),
            "idem_key": f"GSF-P8-BATCH-{uuid.uuid4().hex}",
            "items": [
                {
                    "item_code": ITEM,
                    "item_name": "GSF Phase 8 Batch Item",
                    "qty": qty,
                    "uom": "Nos",
                    "rate": 1500,
                    "warehouse": desk.warehouse,
                    "batch_no": batch_no,
                    "return_against_item": original.items[0].name if original else None,
                }
            ],
            "payments_plan": [
                {
                    "mode_of_payment": mode,
                    "kind": "Cash",
                    "prro_payment_means": "ГОТІВКА",
                    "prro_payment_code": 0,
                    "amount": qty * Decimal("1500"),
                    "currency": frappe.db.get_value(
                        "Company", desk.company, "default_currency"
                    ),
                    "exchange_rate": 1,
                    "status": "Confirmed",
                }
            ],
        }
    ).insert(ignore_permissions=True)
    if is_return:
        from erpnext_ua.group_stock_fifo.services.pos_ua import is_gsf_return

        assert is_gsf_return(order)
    return order


def _post(order, desk):
    invoice = _post_sales_invoice(order, desk)
    order.sales_invoice = invoice.name
    order.status = "Posted"
    order.save(ignore_permissions=True)
    frappe.db.commit()
    drain_reposts()
    return frappe.get_doc("Sales Invoice", invoice.name)


def _assert_batch_sale(
    invoice,
    *,
    batch_no: str,
    qty: Decimal,
    cost: Decimal,
    seller: str,
) -> None:
    assert invoice.company == seller
    assert len(invoice.items) == 1
    row = invoice.items[0]
    assert row.batch_no == batch_no
    assert abs(Decimal(str(row.qty))) == qty
    assert _row_value(invoice.name, row.name) == cost
    layer = frappe.get_doc("GSF Stock Layer", row.get(LAYER_FIELD))
    assert layer.batch_no == batch_no


def _assert_batch_return(
    credit,
    *,
    original,
    batch_no: str,
    qty: Decimal,
    cost: Decimal,
    seller: str,
) -> None:
    assert credit.company == seller
    assert credit.return_against == original.name
    assert len(credit.items) == 1
    row = credit.items[0]
    assert row.batch_no == batch_no
    assert abs(Decimal(str(row.qty))) == qty
    assert _row_value(credit.name, row.name) == cost
    layer = frappe.get_doc("GSF Stock Layer", row.get(LAYER_FIELD))
    assert layer.batch_no == batch_no
    assert layer.return_origin_layer == row.get(RETURN_ORIGIN_LAYER_FIELD)


def _sale_evidence(invoice) -> dict:
    row = invoice.items[0]
    return {
        "invoice": invoice.name,
        "seller": invoice.company,
        "batch_no": row.batch_no,
        "qty": float(abs(Decimal(str(row.qty)))),
        "cost": float(_row_value(invoice.name, row.name)),
    }
