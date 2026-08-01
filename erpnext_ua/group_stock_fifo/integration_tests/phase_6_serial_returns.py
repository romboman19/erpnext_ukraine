"""Clean-site multi-FOP Serial sale and exact return acceptance."""

from __future__ import annotations

import uuid
from decimal import Decimal

import frappe

from erpnext_ua.group_stock_fifo.services.returns import returned_qty
from erpnext_ua.group_stock_fifo.setup.layer_dimension import (
    LAYER_FIELD,
    RETURN_ORIGIN_LAYER_FIELD,
)
from erpnext_ua.ua_pos.api import _post_sales_invoice

from .phase_3_fixture import (
    CUSTOMER,
    GROUP,
    assert_site,
    drain_reposts,
    pool_name,
)
from .phase_3_fixture import (
    build as build_base,
)

ITEM = "GSF-P6-SERIAL"
SERIALS = (
    ("111", 0, Decimal("1000"), "2026-04-01 09:00:00"),
    ("112", 1, Decimal("900"), "2026-04-02 09:00:00"),
    ("113", 0, Decimal("850"), "2026-04-03 09:00:00"),
)


def run() -> dict:
    assert_site()
    if not frappe.db.exists("GSF Company Group", GROUP):
        build_base("BUILD_GSF_PHASE_3")
    firms = _group_companies()
    location = frappe.db.get_value("GSF Physical Location", {"company_group": GROUP}, "name")
    _ensure_base(firms, location)
    _ensure_item()
    for company in firms[:2]:
        _ensure_route_warehouses(company, location)
    receipts = _ensure_serial_receipts(firms)
    drain_reposts()
    frappe.db.commit()
    _recover_fixture_checkouts()

    desk_a, shift_a, employee_a, mode_a = _ensure_pos(firms[0])
    desk_b, shift_b, employee_b, mode_b = _ensure_pos(firms[1])
    scenarios = (
        (desk_a, shift_a, employee_a, mode_a, "112", Decimal("900")),
        (desk_a, shift_a, employee_a, mode_a, "113", Decimal("850")),
        (desk_b, shift_b, employee_b, mode_b, "111", Decimal("1000")),
    )
    sales = []
    for desk, shift, employee, mode, serial_no, expected_cost in scenarios:
        existing = _existing_sale(serial_no, desk.company)
        if existing:
            order, invoice = existing
        else:
            order = _make_order(desk, shift, employee, mode, serial_no)
            invoice = _post(order, desk)
        _assert_serial_sale(invoice, serial_no, expected_cost, desk.company)
        sales.append((order, invoice, expected_cost))

    existing_return = _existing_valid_return(sales)
    if existing_return:
        credit_note, original_invoice, return_serial, expected_cost = existing_return
    else:
        original_order, original_invoice, expected_cost = _next_returnable_sale(sales)
        return_serial = original_invoice.items[0].serial_no
        return_order = _make_order(
            desk_a,
            shift_a,
            employee_a,
            mode_a,
            return_serial,
            original=original_order,
        )
        credit_note = _post(return_order, desk_a)
    _assert_serial_return(
        credit_note, original_invoice, return_serial, expected_cost, firms[0]
    )
    frappe.db.commit()
    return {
        "receipts": receipts,
        "sales": [
            {
                "serial_no": invoice.items[0].serial_no,
                "seller": invoice.company,
                "invoice": invoice.name,
                "cost": float(cost),
            }
            for _order, invoice, cost in sales
        ],
        "return": {
            "serial_no": return_serial,
            "seller": credit_note.company,
            "invoice": credit_note.name,
            "cost": float(expected_cost),
        },
    }


def _group_companies() -> list[str]:
    firms = frappe.get_all(
        "GSF Group Member",
        filters={"parent": GROUP, "enabled": 1},
        pluck="company",
        order_by="idx asc",
    )
    if len(firms) < 2:
        raise RuntimeError("The Serial fixture needs two active GSF member companies")
    return firms


def _ensure_base(firms: list[str], location: str) -> None:
    if not location:
        raise RuntimeError("The Serial fixture needs a GSF Physical Location")
    if not frappe.db.exists("Customer", CUSTOMER):
        frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": CUSTOMER,
                "customer_type": "Individual",
            }
        ).insert(ignore_permissions=True)
    settings = frappe.get_single("GSF Settings")
    if not settings.enabled:
        settings.enabled = 1
        settings.save(ignore_permissions=True)
    if not frappe.db.get_single_value("Selling Settings", "allow_multiple_items"):
        frappe.db.set_single_value("Selling Settings", "allow_multiple_items", 1)
    if not frappe.db.get_single_value(
        "Stock Settings", "enable_serial_and_batch_no_for_item"
    ):
        frappe.db.set_single_value(
            "Stock Settings", "enable_serial_and_batch_no_for_item", 1
        )
    for company in firms[:2]:
        warehouse = pool_name(company)
        if not frappe.db.exists("Warehouse", warehouse):
            frappe.get_doc(
                {"doctype": "Warehouse", "warehouse_name": "GSF P3 Pool", "company": company}
            ).insert(ignore_permissions=True)
        if not frappe.db.exists("GSF Warehouse Binding", warehouse):
            frappe.get_doc(
                {
                    "doctype": "GSF Warehouse Binding",
                    "warehouse": warehouse,
                    "company": company,
                    "company_group": GROUP,
                    "physical_location": location,
                    "manager_app": "GSF",
                    "warehouse_role": "GSF_OWN_POOL",
                    "binding_mode": "MANAGED",
                    "enabled": 1,
                }
            ).insert(ignore_permissions=True)
        if not frappe.db.exists(
            "GSF Location Company Binding",
            {"company_group": GROUP, "physical_location": location, "company": company},
        ):
            frappe.get_doc(
                {
                    "doctype": "GSF Location Company Binding",
                    "company_group": GROUP,
                    "physical_location": location,
                    "company": company,
                    "enabled": 1,
                    "can_purchase": 1,
                    "can_sell": 1,
                    "own_pool_warehouse": warehouse,
                }
            ).insert(ignore_permissions=True)


def _ensure_item() -> None:
    if frappe.db.exists("Item", ITEM):
        return
    frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": ITEM,
            "item_name": "GSF Phase 6 Serial Item",
            "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
            "stock_uom": "Nos",
            "is_stock_item": 1,
            "has_serial_no": 1,
        }
    ).insert(ignore_permissions=True)


def _ensure_route_warehouses(company: str, location: str) -> None:
    for role, label in (
        ("GSF_SALE_STAGE", "GSF P6 Stage"),
        ("GSF_RETURN_QUARANTINE", "GSF P6 Return"),
    ):
        warehouse = _warehouse_name(company, label)
        if not frappe.db.exists("Warehouse", warehouse):
            frappe.get_doc(
                {"doctype": "Warehouse", "warehouse_name": label, "company": company}
            ).insert(ignore_permissions=True)
        if not frappe.db.exists("GSF Warehouse Binding", warehouse):
            frappe.get_doc(
                {
                    "doctype": "GSF Warehouse Binding",
                    "warehouse": warehouse,
                    "company": company,
                    "company_group": GROUP,
                    "physical_location": location,
                    "manager_app": "GSF",
                    "warehouse_role": role,
                    "binding_mode": "MANAGED",
                    "enabled": 1,
                }
            ).insert(ignore_permissions=True)
        if role == "GSF_SALE_STAGE" and not frappe.db.exists(
            "GSF Staging Lane", {"warehouse": warehouse}
        ):
            frappe.get_doc(
                {
                    "doctype": "GSF Staging Lane",
                    "lane_code": f"P6-{frappe.db.get_value('Company', company, 'abbr')}",
                    "company_group": GROUP,
                    "physical_location": location,
                    "company": company,
                    "warehouse": warehouse,
                    "consumer_type": "MANUAL",
                    "enabled": 1,
                    "status": "AVAILABLE",
                }
            ).insert(ignore_permissions=True)


def _ensure_serial_receipts(firms: list[str]) -> list[str]:
    from erpnext_ua.group_stock_fifo.spikes.stock_setup import ensure_clearing_account

    receipts = []
    for serial_no, company_index, rate, posting in SERIALS:
        existing = frappe.db.get_value(
            "Stock Entry",
            {"remarks": f"GSF-P6-SERIAL-{serial_no}", "docstatus": 1},
            "name",
        )
        if existing:
            receipts.append(existing)
            continue
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
                "remarks": f"GSF-P6-SERIAL-{serial_no}",
                "items": [
                    {
                        "item_code": ITEM,
                        "qty": 1,
                        "t_warehouse": pool_name(company),
                        "basic_rate": float(rate),
                        "set_basic_rate_manually": 1,
                        "expense_account": ensure_clearing_account(frappe, company),
                        "use_serial_batch_fields": 1,
                        "serial_no": serial_no,
                    }
                ],
            }
        )
        entry.insert(ignore_permissions=True)
        entry.submit()
        receipts.append(entry.name)
    return receipts


def _ensure_pos(company: str):
    suffix = frappe.db.get_value("Company", company, "abbr")
    desk_name = f"GSF P6 POS {suffix}"
    if not frappe.db.exists("POS Cash Desk", desk_name):
        frappe.get_doc(
            {
                "doctype": "POS Cash Desk",
                "desk_name": desk_name,
                "company": company,
                "warehouse": pool_name(company),
                "default_customer": CUSTOMER,
                "status": "Active",
            }
        ).insert(ignore_permissions=True)
    employee = _employee(company)
    shift = frappe.get_doc(
        {
            "doctype": "POS Operational Shift",
            "cash_desk": desk_name,
            "status": "Open",
            "responsible_employee": employee,
            "idem_key": f"GSF-P6-SERIAL-{uuid.uuid4().hex}",
        }
    ).insert(ignore_permissions=True)
    return frappe.get_doc("POS Cash Desk", desk_name), shift, employee, _cash_mode(company)


def _recover_fixture_checkouts() -> None:
    from erpnext_ua.group_stock_fifo.services.checkout import abort

    rows = frappe.db.sql(
        """
        select distinct checkout.name
        from `tabGSF Checkout` checkout
        inner join `tabGSF Checkout Line` line on line.parent = checkout.name
        where line.item_code = %s
          and checkout.status not in ('COMPLETED', 'CANCELLED', 'COMPENSATED')
        """,
        ITEM,
        pluck=True,
    )
    for checkout in rows:
        abort(checkout, reason="serial integration fixture recovery")
    if rows:
        frappe.db.commit()


def _existing_sale(serial_no: str, seller: str):
    row = frappe.db.sql(
        """
        select orders.name as pos_order, orders.sales_invoice
        from `tabPOS Order` orders
        inner join `tabPOS Order Item` item on item.parent = orders.name
        inner join `tabSales Invoice` invoice on invoice.name = orders.sales_invoice
        where item.item_code = %(item_code)s
          and item.serial_no = %(serial_no)s
          and orders.order_type = 'Sale'
          and orders.status = 'Posted'
          and invoice.docstatus = 1
          and invoice.is_return = 0
          and invoice.gsf_managed_sale = 1
          and invoice.company = %(seller)s
        order by orders.creation desc
        limit 1
        """,
        {"item_code": ITEM, "serial_no": serial_no, "seller": seller},
        as_dict=True,
    )
    if not row:
        return None
    return (
        frappe.get_doc("POS Order", row[0].pos_order),
        frappe.get_doc("Sales Invoice", row[0].sales_invoice),
    )


def _existing_valid_return(sales):
    costs = {invoice.name: cost for _order, invoice, cost in sales}
    rows = frappe.db.sql(
        """
        select credit.name, credit.return_against, item.serial_no
        from `tabSales Invoice` credit
        inner join `tabSales Invoice Item` item on item.parent = credit.name
        inner join `tabGSF Stock Layer` layer on layer.name = item.gsf_stock_layer
        where credit.is_return = 1
          and credit.docstatus = 1
          and credit.return_against in %(invoices)s
          and layer.return_origin_layer is not null
        order by credit.creation desc
        limit 1
        """,
        {"invoices": tuple(costs)},
        as_dict=True,
    )
    if not rows:
        return None
    row = rows[0]
    return (
        frappe.get_doc("Sales Invoice", row.name),
        frappe.get_doc("Sales Invoice", row.return_against),
        row.serial_no,
        costs[row.return_against],
    )


def _next_returnable_sale(sales):
    for order, invoice, cost in sales:
        sold_row = invoice.items[0]
        already_returned = returned_qty(invoice.name, {sold_row.name})[sold_row.name]
        if already_returned < abs(Decimal(str(sold_row.qty))):
            return order, invoice, cost
    raise RuntimeError("The fixture has no unreturned Serial sale and no valid return layer")


def _employee(company: str) -> str:
    employee = frappe.db.get_value("Employee", {"company": company, "status": "Active"}, "name")
    if employee:
        return employee
    return frappe.get_doc(
        {
            "doctype": "Employee",
            "first_name": f"GSF {frappe.db.get_value('Company', company, 'abbr')}",
            "company": company,
            "status": "Active",
            "gender": frappe.db.get_value("Gender", {}, "name"),
            "date_of_birth": "1990-01-01",
            "date_of_joining": "2026-01-01",
        }
    ).insert(ignore_permissions=True).name


def _cash_mode(company: str) -> str:
    mode = frappe.db.get_value("Mode of Payment", {"type": "Cash"}, "name")
    account = frappe.db.get_value(
        "Mode of Payment Account", {"parent": mode, "company": company}, "default_account"
    )
    if not account:
        account = frappe.db.get_value("Company", company, "default_cash_account")
        mode_doc = frappe.get_doc("Mode of Payment", mode)
        mode_doc.append("accounts", {"company": company, "default_account": account})
        mode_doc.save(ignore_permissions=True)
    return mode


def _make_order(desk, shift, employee: str, mode: str, serial_no: str, *, original=None):
    is_return = original is not None
    order = frappe.get_doc(
        {
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
            "idem_key": f"GSF-P6-SERIAL-{uuid.uuid4().hex}",
            "items": [
                {
                    "item_code": ITEM,
                    "item_name": "GSF Phase 6 Serial Item",
                    "qty": 1,
                    "uom": "Nos",
                    "rate": 1500,
                    "warehouse": desk.warehouse,
                    "serial_no": serial_no,
                    "return_against_item": original.items[0].name if original else None,
                }
            ],
            "payments_plan": [
                {
                    "mode_of_payment": mode,
                    "kind": "Cash",
                    "prro_payment_means": "ГОТІВКА",
                    "prro_payment_code": 0,
                    "amount": 1500,
                    "currency": frappe.db.get_value("Company", desk.company, "default_currency"),
                    "exchange_rate": 1,
                    "status": "Confirmed",
                }
            ],
        }
    ).insert(ignore_permissions=True)
    if is_return:
        from erpnext_ua.group_stock_fifo.services.pos_ua import is_gsf_return

        if not is_gsf_return(order):
            raise RuntimeError(
                f"Return {order.name} did not resolve {order.return_against} as a GSF sale"
            )
    return order


def _post(order, desk):
    invoice = _post_sales_invoice(order, desk)
    order.sales_invoice = invoice.name
    order.status = "Posted"
    order.save(ignore_permissions=True)
    frappe.db.commit()
    drain_reposts()
    return frappe.get_doc("Sales Invoice", invoice.name)


def _assert_serial_sale(invoice, serial_no: str, cost: Decimal, seller: str) -> None:
    assert invoice.company == seller
    assert len(invoice.items) == 1
    assert invoice.items[0].serial_no == serial_no
    assert _row_value(invoice.name, invoice.items[0].name) == cost


def _assert_serial_return(credit, original, serial_no: str, cost: Decimal, seller: str) -> None:
    row = credit.items[0]
    assert credit.company == seller
    assert credit.return_against == original.name
    assert row.serial_no == serial_no
    assert _row_value(credit.name, row.name) == cost
    layer = frappe.get_doc("GSF Stock Layer", row.get(LAYER_FIELD))
    assert layer.return_origin_layer == row.get(RETURN_ORIGIN_LAYER_FIELD)
    assert layer.serial_numbers == serial_no


def _row_value(invoice: str, row: str) -> Decimal:
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


def _warehouse_name(company: str, label: str) -> str:
    return f"{label} - {frappe.db.get_value('Company', company, 'abbr')}"
