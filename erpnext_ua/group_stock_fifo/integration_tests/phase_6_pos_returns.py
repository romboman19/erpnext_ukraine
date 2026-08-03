"""POS-UA acceptance: three FOP FIFO layers, partial/full receipt returns.

Run only after ``phase_3_fixture.build`` on the dedicated test site::

    docker exec frappe-test-backend-1 bench --site postest.local execute \
      erpnext_ua.group_stock_fifo.integration_tests.phase_6_pos_returns.run
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import frappe

from erpnext_ua.group_stock_fifo.services.returns import returned_qty
from erpnext_ua.group_stock_fifo.setup.layer_dimension import (
    LAYER_FIELD,
    MANAGED_RETURN_FIELD,
    MANAGED_SALE_FIELD,
    RETURN_ORIGIN_LAYER_FIELD,
)
from erpnext_ua.ua_pos.api import _post_sales_invoice
from erpnext_ua.ua_pos.barcode import decode_lookup_token, encode_lookup_token

from .phase_3_fixture import (
    CUSTOMER,
    ITEM,
    assert_site,
    companies,
    drain_reposts,
    pool_name,
)

DESK = "GSF Phase 6 POS"
SALE_RATE = Decimal("2000")
NET_RATE = Decimal("1900")


def run() -> dict:
    """Execute and assert the complete sale/return lineage on ``postest.local``."""
    assert_site()
    firms = companies()
    seller = firms[2]
    desk, shift, employee, payment_mode = _ensure_pos(seller)
    run_id = uuid.uuid4().hex[:10]

    sale_order = _make_order(
        run_id,
        desk=desk,
        shift=shift,
        employee=employee,
        qty=Decimal("6"),
        discount=Decimal("600"),
        payment_mode=payment_mode,
    )
    sale = _post(sale_order, desk)
    sale_rows = list(sale.items)
    _assert_sale_fifo(sale, sale_rows, firms)
    receipt_barcode = encode_lookup_token(sale_order.lookup_token)
    assert decode_lookup_token(receipt_barcode) == sale_order.lookup_token
    assert frappe.db.get_value("POS Order", {"lookup_token": sale_order.lookup_token}, "name") == sale_order.name

    returns = []
    for index, qty in enumerate((Decimal("1"), Decimal("3"), Decimal("2")), start=1):
        order = _make_order(
            f"{run_id}-R{index}",
            desk=desk,
            shift=shift,
            employee=employee,
            qty=qty,
            discount=qty * (SALE_RATE - NET_RATE),
            payment_mode=payment_mode,
            original=sale_order,
        )
        credit_note = _post(order, desk)
        _assert_return(credit_note, sale, seller)
        returns.append(credit_note)

    sold_names = {row.name for row in sale_rows}
    totals = returned_qty(sale.name, sold_names)
    assert totals == {row.name: abs(Decimal(str(row.qty))) for row in sale_rows}, totals
    _assert_all_returned_stock_belongs_to_seller(returns, seller)

    second_order = _make_order(
        f"{run_id}-SECOND",
        desk=desk,
        shift=shift,
        employee=employee,
        qty=Decimal("5"),
        discount=Decimal("0"),
        payment_mode=payment_mode,
        rate=Decimal("2100"),
    )
    second_sale = _post(second_order, desk)
    second_layers = [(row.get(LAYER_FIELD), Decimal(str(row.qty))) for row in second_sale.items]
    assert second_layers[0] == (sale_rows[2].get(LAYER_FIELD), Decimal("4")), second_layers
    assert second_layers[1][0] == returns[0].items[0].get(LAYER_FIELD), second_layers
    assert second_layers[1][1] == Decimal("1"), second_layers

    frappe.db.commit()
    return {
        "seller": seller,
        "receipt": {"pos_order": sale_order.name, "barcode": receipt_barcode},
        "sale": {
            "invoice": sale.name,
            "managed": sale.get(MANAGED_SALE_FIELD),
            "qty_by_source_fop": [
                {
                    "company": frappe.db.get_value("GSF Stock Layer", row.get(LAYER_FIELD), "origin_company"),
                    "qty": row.qty,
                    "cost": float(_row_sle_value(sale.name, row.name)),
                }
                for row in sale_rows
            ],
        },
        "returns": [
            {
                "invoice": credit.name,
                "qty": abs(sum(Decimal(str(row.qty)) for row in credit.items)),
                "layers": [
                    {
                        "new": row.get(LAYER_FIELD),
                        "sold": row.get(RETURN_ORIGIN_LAYER_FIELD),
                        "root": frappe.db.get_value("GSF Stock Layer", row.get(LAYER_FIELD), "lineage_root_layer"),
                        "qty": abs(row.qty),
                        "value": float(_row_sle_value(credit.name, row.name)),
                    }
                    for row in credit.items
                ],
            }
            for credit in returns
        ],
        "next_global_fifo_sale": {
            "invoice": second_sale.name,
            "layers": [{"layer": layer, "qty": float(qty)} for layer, qty in second_layers],
        },
    }


def _ensure_pos(company: str):
    employee = frappe.db.get_value("Employee", {"status": "Active"}, "name")
    if not employee:
        gender = frappe.db.get_value("Gender", {}, "name")
        if not gender:
            raise RuntimeError("The POS return fixture needs one configured Gender")
        employee = (
            frappe.get_doc(
                {
                    "doctype": "Employee",
                    "first_name": "GSF POS Test Cashier",
                    "company": company,
                    "status": "Active",
                    "gender": gender,
                    "date_of_birth": "1990-01-01",
                    "date_of_joining": "2026-01-01",
                }
            )
            .insert(ignore_permissions=True)
            .name
        )
    if not frappe.db.exists("POS Cash Desk", DESK):
        frappe.get_doc(
            {
                "doctype": "POS Cash Desk",
                "desk_name": DESK,
                "company": company,
                "warehouse": pool_name(company),
                "default_customer": CUSTOMER,
                "status": "Active",
            }
        ).insert(ignore_permissions=True)
    shift = frappe.get_doc(
        {
            "doctype": "POS Operational Shift",
            "cash_desk": DESK,
            "status": "Open",
            "responsible_employee": employee,
            "idem_key": f"GSF-P6-{uuid.uuid4().hex}",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
    return frappe.get_doc("POS Cash Desk", DESK), shift, employee, _cash_mode(company)


def _cash_mode(company: str) -> str:
    mode = frappe.db.get_value("Mode of Payment", {"type": "Cash"}, "name")
    if not mode:
        raise RuntimeError("The POS return fixture needs one Cash Mode of Payment")
    account = frappe.db.get_value("Mode of Payment Account", {"parent": mode, "company": company}, "default_account")
    if not account:
        account = frappe.db.get_value("Company", company, "default_cash_account") or frappe.db.get_value(
            "Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name"
        )
        if not account:
            raise RuntimeError(f"The POS return fixture needs a Cash account for {company}")
        mode_doc = frappe.get_doc("Mode of Payment", mode)
        mode_doc.append("accounts", {"company": company, "default_account": account})
        mode_doc.save(ignore_permissions=True)
    return mode


def _make_order(
    key: str,
    *,
    desk,
    shift,
    employee: str,
    qty: Decimal,
    discount: Decimal,
    payment_mode: str,
    rate: Decimal = SALE_RATE,
    original=None,
):
    is_return = original is not None
    values = {
        "doctype": "POS Order",
        "cash_desk": desk.name,
        "operational_shift": shift.name,
        "employee": employee,
        "customer": CUSTOMER,
        "order_type": "Return" if is_return else "Sale",
        "return_against": original.name if original else None,
        "fiscal_mode": "Non Fiscal",
        "status": "Paid",
        "lookup_token": str(uuid.uuid4()),
        "idem_key": f"GSF-P6-{key}",
        "items": [
            {
                "item_code": ITEM,
                "item_name": "GSF Phase 3 Item",
                "qty": float(qty),
                "uom": "Nos",
                "rate": float(rate),
                "warehouse": desk.warehouse,
                "discount_amount": float(discount),
                "return_against_item": original.items[0].name if original else None,
            }
        ],
        "payments_plan": [
            {
                "mode_of_payment": payment_mode,
                "kind": "Cash",
                "prro_payment_form": "ГОТІВКА",
                "prro_payment_means": "ГОТІВКА",
                "prro_payment_code": 0,
                "payment_context": "Звичайна оплата",
                "amount": float(qty * rate - discount),
                "currency": frappe.db.get_value("Company", desk.company, "default_currency"),
                "exchange_rate": 1,
                "status": "Confirmed",
            }
        ],
    }
    return frappe.get_doc(values).insert(ignore_permissions=True)


def _post(order, desk):
    invoice = _post_sales_invoice(order, desk)
    order.sales_invoice = invoice.name
    order.status = "Posted"
    order.save(ignore_permissions=True)
    frappe.db.commit()
    drain_reposts()
    return frappe.get_doc("Sales Invoice", invoice.name)


def _assert_sale_fifo(invoice, rows: list, firms: list[str]) -> None:
    assert invoice.company == firms[2]
    assert invoice.get(MANAGED_SALE_FIELD)
    assert [Decimal(str(row.qty)) for row in rows] == [Decimal("2"), Decimal("3"), Decimal("1")]
    assert [frappe.db.get_value("GSF Stock Layer", row.get(LAYER_FIELD), "origin_company") for row in rows] == firms
    assert [_row_sle_value(invoice.name, row.name) for row in rows] == [
        Decimal("2000"),
        Decimal("3300"),
        Decimal("1200"),
    ]
    assert Decimal(str(invoice.grand_total)) == Decimal("11400")


def _assert_return(credit_note, original, seller: str) -> None:
    assert credit_note.company == seller
    assert credit_note.return_against == original.name
    assert credit_note.get(MANAGED_RETURN_FIELD)
    for row in credit_note.items:
        new_layer = frappe.get_doc("GSF Stock Layer", row.get(LAYER_FIELD))
        assert new_layer.origin_company == seller
        assert new_layer.origin_document == credit_note.name
        assert new_layer.return_origin_layer == row.get(RETURN_ORIGIN_LAYER_FIELD)
        assert new_layer.lineage_root_layer == row.get(RETURN_ORIGIN_LAYER_FIELD)
        source_value = _row_sle_value(original.name, row.sales_invoice_item)
        source_qty = abs(Decimal(str(frappe.db.get_value("Sales Invoice Item", row.sales_invoice_item, "qty"))))
        expected = source_value * abs(Decimal(str(row.qty))) / source_qty
        assert _row_sle_value(credit_note.name, row.name) == expected


def _assert_all_returned_stock_belongs_to_seller(returns: list, seller: str) -> None:
    pool = pool_name(seller)
    layers = [row.get(LAYER_FIELD) for credit in returns for row in credit.items]
    balances = frappe.get_all(
        "GSF Layer Balance",
        filters={"stock_layer": ("in", layers), "company": seller, "warehouse": pool},
        fields=["stock_layer", "actual_qty_cache"],
    )
    assert sum(Decimal(str(row.actual_qty_cache)) for row in balances) == Decimal("6"), balances
    assert {row.stock_layer for row in balances} == set(layers), balances


def _row_sle_value(invoice: str, row: str) -> Decimal:
    value = frappe.db.sql(
        """
        select coalesce(sum(stock_value_difference), 0)
        from `tabStock Ledger Entry`
        where voucher_type='Sales Invoice' and voucher_no=%s
          and voucher_detail_no=%s and is_cancelled=0
        """,
        (invoice, row),
    )[0][0]
    return abs(Decimal(str(value or 0)))
