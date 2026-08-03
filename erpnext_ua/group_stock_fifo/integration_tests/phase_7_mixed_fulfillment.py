"""Acceptance probe for mixed GSF/consignment/commission sale fulfillment.

The probe is deliberately restricted to dedicated acceptance sites.  Every run
uses new item and document identities, so prior acceptance evidence cannot
make a later run pass by accident.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import frappe
from frappe.utils import now_datetime, nowdate

from erpnext_ua.consignment_and_commission.spikes.accounting import (
    _ensure_accounts,
    _ensure_supplier,
)
from erpnext_ua.group_stock_fifo.services.fulfillment_channels import (
    fulfill_sales_invoice_document,
    fulfill_sales_order,
)
from erpnext_ua.group_stock_fifo.setup.cc_discovery import audit_cc_bindings
from erpnext_ua.group_stock_fifo.spikes.fixtures import FOPS
from erpnext_ua.ua_pos.api import _post_sales_invoice

ALLOWED_SITES = {"gsfaccept.local", "fifoaccept.local", "integration.local"}
RATE = Decimal("1500")


def run() -> dict:
    if frappe.local.site not in ALLOWED_SITES:
        raise RuntimeError(
            f"This acceptance probe is restricted to {sorted(ALLOWED_SITES)}"
        )
    run_id = uuid.uuid4().hex[:8].upper()
    company_a, company_b = (fop.company for fop in FOPS[:2])
    location = frappe.db.get_value(
        "GSF Physical Location", {"location_code": "P3"}, "name"
    )
    if not location:
        raise RuntimeError("Phase 3 physical location is required")
    customer = _ensure_customer(run_id)
    desks = {
        company: _ensure_pos_route(company, customer, location, run_id)
        for company in (company_a, company_b)
    }
    cc = {
        company_a: _ensure_cc_route(company_a, location, desks[company_a][0], run_id),
        company_b: _ensure_cc_route(company_b, location, desks[company_b][0], run_id),
    }
    _assert_cc_registry(cc, location)
    _enable_settings(company_a, cc[company_a]["location"])
    mode = _ensure_cash_mode((company_a, company_b))

    manual_item = _ensure_item(f"P7-MAN-{run_id}")
    _receive_four_routes(
        manual_item,
        company_a=company_a,
        company_b=company_b,
        location=location,
        cc=cc,
        run_id=f"{run_id}-MAN",
    )
    _assert_domain_inventory(manual_item, location)
    manual = _manual_invoice_sale(
        item_code=manual_item,
        company=company_a,
        customer=customer,
        location=location,
        run_id=run_id,
    )
    _assert_route_order(manual["checkout"], ("GSF", "CC", "CC"))
    _assert_mixed_sale(manual["invoices"], company_a, company_b, seller_company=company_a)

    sales_order_item = _ensure_item(f"P7-SO-{run_id}")
    _receive_four_routes(
        sales_order_item,
        company_a=company_a,
        company_b=company_b,
        location=location,
        cc=cc,
        run_id=f"{run_id}-SO",
    )
    sales_order = _sales_order_sale(
        item_code=sales_order_item,
        company=company_b,
        customer=customer,
        location=location,
    )
    _assert_route_order(sales_order["checkout"], ("GSF", "CC", "CC"))
    _assert_mixed_sale(
        sales_order["invoices"], company_a, company_b, seller_company=company_b
    )
    _assert_sales_order_links(sales_order["source"], sales_order["invoices"])

    pos_item = _ensure_item(f"P7-POS-{run_id}")
    _receive_four_routes(
        pos_item,
        company_a=company_a,
        company_b=company_b,
        location=location,
        cc=cc,
        run_id=f"{run_id}-POS",
    )
    sale_order, sale_invoices = _pos_sale(
        item_code=pos_item,
        customer=customer,
        desk=desks[company_a],
        mode=mode,
        run_id=run_id,
    )
    _assert_mixed_sale(sale_invoices, company_a, company_b, seller_company=company_a)
    partial_order, partial_returns = _pos_return(
        original=sale_order,
        qty=Decimal("3"),
        desk=desks[company_a],
        mode=mode,
        key=f"{run_id}-PARTIAL",
    )
    full_order, final_returns = _pos_return(
        original=sale_order,
        qty=Decimal("1"),
        desk=desks[company_a],
        mode=mode,
        key=f"{run_id}-FINAL",
    )
    _assert_returns(partial_returns + final_returns, sale_invoices)
    frappe.db.commit()
    return {
        "run_id": run_id,
        "manual_invoice": _evidence(manual["checkout"], manual["invoices"]),
        "sales_order": _evidence(sales_order["checkout"], sales_order["invoices"]),
        "pos_sale": _evidence(sale_order.gsf_checkout, sale_invoices),
        "returns": {
            "partial_order": partial_order.name,
            "partial_invoices": [row.name for row in partial_returns],
            "final_order": full_order.name,
            "final_invoices": [row.name for row in final_returns],
            "seller_companies": [row.company for row in partial_returns + final_returns],
        },
    }


def _ensure_customer(run_id: str) -> str:
    from erpnext_ua.tests.integrations.frappe_fixtures import (
        ensure_customer_master_links,
    )

    name = f"P7 Customer {run_id}"
    customer_group, territory = ensure_customer_master_links()
    return frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": name,
            "customer_type": "Individual",
            "customer_group": customer_group,
            "territory": territory,
        }
    ).insert(ignore_permissions=True).name


def _ensure_item(item_code: str) -> str:
    return frappe.get_doc(
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_code,
            "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
            "stock_uom": "Nos",
            "is_stock_item": 1,
        }
    ).insert(ignore_permissions=True).name


def _pool(company: str, location: str) -> str:
    warehouse = frappe.db.get_value(
        "GSF Warehouse Binding",
        {
            "company": company,
            "physical_location": location,
            "manager_app": "GSF",
            "warehouse_role": "GSF_OWN_POOL",
            "enabled": 1,
        },
        "warehouse",
    )
    if not warehouse:
        raise RuntimeError(f"{company} has no GSF OWN pool")
    return warehouse


def _ensure_pos_route(company: str, customer: str, location: str, run_id: str):
    pool = _pool(company, location)
    abbr = frappe.db.get_value("Company", company, "abbr")
    stage = _warehouse(company, f"P7 Stage {run_id}")
    quarantine = _warehouse(company, f"P7 Return {run_id}")
    for warehouse, role in ((stage, "GSF_SALE_STAGE"), (quarantine, "GSF_RETURN_QUARANTINE")):
        frappe.get_doc(
            {
                "doctype": "GSF Warehouse Binding",
                "warehouse": warehouse,
                "company": company,
                "company_group": frappe.db.get_value("GSF Physical Location", location, "company_group"),
                "physical_location": location,
                "manager_app": "GSF",
                "warehouse_role": role,
                "binding_mode": "MANAGED",
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)
    frappe.get_doc(
        {
            "doctype": "GSF Staging Lane",
            "lane_code": f"P7-{run_id}-{abbr}",
            "company_group": frappe.db.get_value("GSF Physical Location", location, "company_group"),
            "physical_location": location,
            "company": company,
            "warehouse": stage,
            "consumer_type": "MANUAL",
            "enabled": 1,
            "status": "AVAILABLE",
        }
    ).insert(ignore_permissions=True)
    desk = frappe.get_doc(
        {
            "doctype": "POS Cash Desk",
            "desk_name": f"P7 Desk {run_id} {abbr}",
            "company": company,
            "warehouse": pool,
            "default_customer": customer,
            "status": "Active",
        }
    ).insert(ignore_permissions=True)
    employee = frappe.db.get_value("Employee", {"status": "Active", "company": company}, "name")
    if not employee:
        employee = frappe.get_doc(
            {
                "doctype": "Employee",
                "first_name": f"P7 {abbr}",
                "company": company,
                "status": "Active",
                "gender": frappe.db.get_value("Gender", {}, "name"),
                "date_of_birth": "1990-01-01",
                "date_of_joining": nowdate(),
            }
        ).insert(ignore_permissions=True).name
    shift = frappe.get_doc(
        {
            "doctype": "POS Operational Shift",
            "cash_desk": desk.name,
            "status": "Open",
            "responsible_employee": employee,
            "idem_key": f"P7-SHIFT-{run_id}-{abbr}",
        }
    ).insert(ignore_permissions=True)
    return desk, shift, employee


def _warehouse(company: str, title: str) -> str:
    return frappe.get_doc(
        {"doctype": "Warehouse", "warehouse_name": title, "company": company}
    ).insert(ignore_permissions=True).name


def _ensure_cc_route(company: str, location: str, desk, run_id: str) -> dict:
    warehouses = {
        model: _warehouse(company, f"P7 CC {model.title()} {run_id}")
        for model in ("OWN", "COMMISSION", "CONSIGNMENT")
    }
    accounts = _ensure_accounts(frappe, company, require_payment_accounts=False)
    abbr = frappe.db.get_value("Company", company, "abbr")
    supplier = _ensure_supplier(
        frappe,
        company=company,
        supplier_name=f"P7 Supplier {run_id} {abbr}",
        payable_account=accounts["supplier_payable"],
    )
    partner = frappe.get_doc(
        {
            "doctype": "CC Partner Profile",
            "partner_name": f"P7 Partner {run_id} {abbr}",
            "supplier": supplier,
            "allowed_relationship_models": "BOTH",
            "default_currency": frappe.db.get_value("Company", company, "default_currency"),
            "default_settlement_deadline_days": 7,
        }
    ).insert(ignore_permissions=True)
    cc_location = frappe.get_doc(
        {
            "doctype": "CC Location",
            "location_name": f"P7 CC {run_id} {abbr}",
            "company": company,
            "legal_entity_type": "Company",
            "legal_entity_name": company,
            "own_warehouse": warehouses["OWN"],
            "commission_warehouse": warehouses["COMMISSION"],
            "consignment_warehouse": warehouses["CONSIGNMENT"],
            "gsf_physical_location": location,
            "read_stock_enabled": 1,
            "pos_cash_desk": desk.name,
        }
    ).insert(ignore_permissions=True)
    if not frappe.db.exists("CC Account Mapping", company):
        frappe.get_doc(
            {
                "doctype": "CC Account Mapping",
                "company": company,
                "off_balance_goods_account": accounts["off_balance_goods"],
                "gross_proceeds_clearing_account": accounts["commission_gross_proceeds"],
                "commission_revenue_account": accounts["commission_revenue"],
                "principal_proceeds_deduction_account": accounts["principal_proceeds_deduction"],
                "unreported_commission_liability_account": accounts["unreported_commission_liability"],
                "unreported_consignment_liability_account": accounts["unreported_consignment_liability"],
                "default_supplier_payable_account": accounts["supplier_payable"],
            }
        ).insert(ignore_permissions=True)
    contracts = {}
    for model in ("CONSIGNMENT", "COMMISSION"):
        contracts[model] = frappe.get_doc(
            {
                "doctype": "CC Contract",
                "contract_title": f"P7 {model} {run_id} {abbr}",
                "status": "ACTIVE",
                "partner_profile": partner.name,
                "company": company,
                "location": cc_location.name,
                "relationship_model": model,
                "currency": frappe.db.get_value("Company", company, "default_currency"),
                "commission_rate": 15 if model == "COMMISSION" else 0,
                "valid_from": nowdate(),
                "settlement_frequency": "MONTHLY",
                "settlement_deadline_days": 7,
                "fiscal_policy": "AUTO",
                "price_authority": "COMPANY",
            }
        ).insert(ignore_permissions=True).name
    return {"location": cc_location.name, "contracts": contracts}


def _enable_settings(company: str, location: str) -> None:
    for doctype in ("Buying Settings", "Selling Settings"):
        frappe.db.set_single_value(doctype, "allow_multiple_items", 1)
    settings = frappe.get_single("CC Settings")
    settings.enabled = 1
    settings.enable_commission = 1
    settings.enable_consignment = 1
    settings.default_company = company
    settings.default_location = location
    settings.reservation_ttl_minutes = 60
    settings.allocation_retry_limit = 3
    settings.save(ignore_permissions=True)
    gsf = frappe.get_single("GSF Settings")
    gsf.enabled = 1
    gsf.save(ignore_permissions=True)


def _ensure_cash_mode(companies: tuple[str, str]) -> str:
    mode = frappe.db.get_value("Mode of Payment", {"type": "Cash"}, "name")
    doc = frappe.get_doc("Mode of Payment", mode)
    configured = {row.company for row in doc.accounts}
    for company in companies:
        if company in configured:
            continue
        account = frappe.db.get_value("Company", company, "default_cash_account") or frappe.db.get_value(
            "Account", {"company": company, "account_type": "Cash", "is_group": 0}, "name"
        )
        doc.append("accounts", {"company": company, "default_account": account})
    doc.save(ignore_permissions=True)
    return mode


def _receive_four_routes(
    item_code: str,
    *,
    company_a: str,
    company_b: str,
    location: str,
    cc: dict,
    run_id: str,
) -> None:
    from erpnext_ua.group_stock_fifo.spikes.stock_setup import ensure_clearing_account

    for index, (company, rate) in enumerate(((company_a, 1000), (company_b, 900)), 1):
        entry = frappe.get_doc(
            {
                "doctype": "Stock Entry",
                "stock_entry_type": "Material Receipt",
                "purpose": "Material Receipt",
                "company": company,
                "set_posting_time": 1,
                "posting_date": nowdate(),
                "posting_time": f"00:0{index}:00",
                "gsf_managed": 1,
                "remarks": f"{run_id}-BUYOUT-{index}",
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "t_warehouse": _pool(company, location),
                        "basic_rate": rate,
                        "set_basic_rate_manually": 1,
                        "expense_account": ensure_clearing_account(frappe, company),
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        entry.submit()
    for index, (company, model, value) in enumerate(
        ((company_a, "CONSIGNMENT", 850), (company_b, "COMMISSION", 800)),
        3,
    ):
        receipt = frappe.get_doc(
            {
                "doctype": "CC Receipt",
                "posting_date": nowdate(),
                "posting_time": f"00:0{index}:00",
                "contract": cc[company]["contracts"][model],
                "items": [
                    {
                        "item_code": item_code,
                        "qty": 1,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "accounting_unit_value": value,
                    }
                ],
            }
        ).insert(ignore_permissions=True)
        receipt.submit()
        if model == "CONSIGNMENT":
            lot = receipt.items[0].stock_lot
            valid_from = frappe.db.get_value("CC Stock Lot", lot, "received_datetime")
            price = frappe.get_doc(
                {
                    "doctype": "CC Price Version",
                    "stock_lot": lot,
                    "partner_rate": value,
                    "valid_from": valid_from,
                }
            ).insert(ignore_permissions=True)
            price.submit()
    frappe.db.commit()
    from erpnext_ua.group_stock_fifo.integration_tests.phase_3_fixture import drain_reposts

    drain_reposts()
    frappe.db.commit()


def _manual_invoice_sale(*, item_code: str, company: str, customer: str, location: str, run_id: str):
    currency = frappe.db.get_value("Company", company, "default_currency")
    price_list = frappe.db.get_value(
        "Price List",
        {"selling": 1, "enabled": 1, "currency": currency},
        "name",
    )
    source = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "company": company,
            "customer": customer,
            "currency": currency,
            "conversion_rate": 1,
            "selling_price_list": price_list,
            "price_list_currency": currency,
            "plc_conversion_rate": 1,
            "ua_fulfillment_physical_location": location,
            "items": [{"item_code": item_code, "qty": 4, "rate": RATE}],
        }
    ).insert(ignore_permissions=True)
    checkout = fulfill_sales_invoice_document(source, sales_channel="MANUAL_INVOICE")
    names = json.loads(checkout.sales_invoices)
    return {"checkout": checkout.name, "invoices": [frappe.get_doc("Sales Invoice", name) for name in names]}


def _pos_sale(*, item_code: str, customer: str, desk, mode: str, run_id: str):
    cash_desk, shift, employee = desk
    order = _order(
        key=f"{run_id}-SALE",
        item_code=item_code,
        qty=Decimal("4"),
        customer=customer,
        desk=cash_desk,
        shift=shift,
        employee=employee,
        mode=mode,
    )
    primary = _post_sales_invoice(order, cash_desk)
    order.sales_invoice = primary.name
    order.status = "Posted"
    order.save(ignore_permissions=True)
    frappe.db.commit()
    names = json.loads(order.sales_invoices_json)
    return order, [frappe.get_doc("Sales Invoice", name) for name in names]


def _sales_order_sale(*, item_code: str, company: str, customer: str, location: str):
    currency = frappe.db.get_value("Company", company, "default_currency")
    price_list = frappe.db.get_value(
        "Price List",
        {"selling": 1, "enabled": 1, "currency": currency},
        "name",
    )
    source = frappe.get_doc(
        {
            "doctype": "Sales Order",
            "company": company,
            "customer": customer,
            "transaction_date": nowdate(),
            "delivery_date": nowdate(),
            "currency": currency,
            "conversion_rate": 1,
            "selling_price_list": price_list,
            "price_list_currency": currency,
            "plc_conversion_rate": 1,
            "ua_fulfillment_physical_location": location,
            "items": [
                {
                    "item_code": item_code,
                    "qty": 4,
                    "rate": RATE,
                    "delivery_date": nowdate(),
                    "warehouse": _pool(company, location),
                }
            ],
        }
    ).insert(ignore_permissions=True)
    source.submit()
    result = fulfill_sales_order(source.name)
    names = result["sales_invoices"]
    return {
        "source": source.name,
        "checkout": result["fulfillment"],
        "invoices": [frappe.get_doc("Sales Invoice", name) for name in names],
    }


def _pos_return(*, original, qty: Decimal, desk, mode: str, key: str):
    cash_desk, shift, employee = desk
    order = _order(
        key=key,
        item_code=original.items[0].item_code,
        qty=qty,
        customer=original.customer,
        desk=cash_desk,
        shift=shift,
        employee=employee,
        mode=mode,
        original=original,
    )
    primary = _post_sales_invoice(order, cash_desk)
    order.sales_invoice = primary.name
    order.status = "Posted"
    order.save(ignore_permissions=True)
    frappe.db.commit()
    names = json.loads(order.sales_invoices_json)
    return order, [frappe.get_doc("Sales Invoice", name) for name in names]


def _order(*, key, item_code, qty, customer, desk, shift, employee, mode, original=None):
    return frappe.get_doc(
        {
            "doctype": "POS Order",
            "cash_desk": desk.name,
            "operational_shift": shift.name,
            "employee": employee,
            "customer": customer,
            "order_type": "Return" if original else "Sale",
            "return_against": original.name if original else None,
            "fiscal_mode": "Non Fiscal",
            "status": "Paid",
            "lookup_token": str(uuid.uuid4()),
            "idem_key": f"P7-{key}",
            "items": [
                {
                    "item_code": item_code,
                    "item_name": item_code,
                    "qty": qty,
                    "uom": "Nos",
                    "rate": RATE,
                    "warehouse": desk.warehouse,
                    "return_against_item": original.items[0].name if original else None,
                }
            ],
            "payments_plan": [
                {
                    "mode_of_payment": mode,
                    "kind": "Cash",
                    "prro_payment_form": "ГОТІВКА",
                    "prro_payment_means": "ГОТІВКА",
                    "prro_payment_code": 0,
                    "payment_context": "Звичайна оплата",
                    "amount": qty * RATE,
                    "currency": frappe.db.get_value("Company", desk.company, "default_currency"),
                    "exchange_rate": 1,
                    "status": "Confirmed",
                }
            ],
        }
    ).insert(ignore_permissions=True)


def _assert_route_order(checkout_name: str, providers: tuple[str, ...]) -> None:
    routes = json.loads(frappe.db.get_value("GSF Checkout", checkout_name, "route_manifest"))
    assert tuple(row["provider_id"] for row in routes) == providers, routes


def _assert_mixed_sale(
    invoices: list,
    company_a: str,
    company_b: str,
    *,
    seller_company: str,
) -> None:
    assert len(invoices) == 3, [row.name for row in invoices]
    assert [row.company for row in invoices] == [seller_company, company_a, company_b]
    gsf = invoices[0]
    assert all(row.gsf_stock_layer and not row.cc_stock_lot for row in gsf.items)
    costs = [
        abs(Decimal(str(frappe.db.sql(
            "select coalesce(sum(stock_value_difference),0) from `tabStock Ledger Entry` "
            "where voucher_type='Sales Invoice' and voucher_no=%s and voucher_detail_no=%s",
            (gsf.name, row.name),
        )[0][0] or 0)))
        for row in gsf.items
    ]
    assert costs == [Decimal("1000"), Decimal("900")], costs
    cc_models = [
        row.relationship_model
        for invoice in invoices[1:]
        for row in frappe.get_all(
            "CC Sale Allocation",
            filters={"sales_invoice": invoice.name},
            fields=["relationship_model"],
        )
    ]
    assert cc_models == ["CONSIGNMENT", "COMMISSION"], cc_models
    for invoice in invoices[1:]:
        assert all(row.cc_stock_lot and not row.gsf_stock_layer for row in invoice.items)
    assert frappe.db.count(
        "GSF Stock Layer", {"item_code": gsf.items[0].item_code}
    ) == 2


def _assert_cc_registry(cc: dict, physical_location: str) -> None:
    issues = audit_cc_bindings()
    assert not issues, issues
    warehouses = {
        warehouse
        for route in cc.values()
        for warehouse in frappe.db.get_value(
            "CC Location",
            route["location"],
            ["own_warehouse", "commission_warehouse", "consignment_warehouse"],
        )
    }
    bindings = frappe.get_all(
        "GSF Warehouse Binding",
        filters={"warehouse": ("in", tuple(warehouses))},
        fields=[
            "warehouse",
            "manager_app",
            "binding_mode",
            "physical_location",
            "source_doctype",
            "enabled",
        ],
    )
    assert len(bindings) == 6, bindings
    assert all(
        row.manager_app == "CC"
        and row.binding_mode == "DISCOVERED_EXTERNAL"
        and row.physical_location == physical_location
        and row.source_doctype == "CC Location"
        and row.enabled
        for row in bindings
    ), bindings
    warehouse = sorted(warehouses)[0]
    company = frappe.db.get_value("Warehouse", warehouse, "company")
    company_group = frappe.db.get_value(
        "GSF Physical Location", physical_location, "company_group"
    )
    attempted_overlap = frappe.get_doc(
        {
            "doctype": "GSF Warehouse Binding",
            "warehouse": warehouse,
            "company": company,
            "company_group": company_group,
            "physical_location": physical_location,
            "manager_app": "GSF",
            "warehouse_role": "GSF_OWN_POOL",
            "binding_mode": "MANAGED",
            "enabled": 1,
        }
    )
    try:
        attempted_overlap.validate()
    except frappe.ValidationError as error:
        assert "already belongs to stock domain CC" in str(error)
    else:
        raise AssertionError("CC warehouse accepted as a GSF own pool")


def _assert_domain_inventory(item_code: str, physical_location: str) -> None:
    company_group = frappe.db.get_value(
        "GSF Physical Location", physical_location, "company_group"
    )
    count = frappe.get_doc(
        {
            "doctype": "GSF Physical Stock Count",
            "status": "DRAFT",
            "company_group": company_group,
            "physical_location": physical_location,
            "item_code": item_code,
            "count_datetime": now_datetime(),
            "counted_qty": 4,
            "adjustment_policy": "MANUAL_APPROVAL",
        }
    )
    count.validate()
    assert Decimal(str(count.gsf_total)) == Decimal("2")
    assert Decimal(str(count.cc_total)) == Decimal("2")
    assert Decimal(str(count.external_total)) == Decimal("0")
    assert Decimal(str(count.system_total)) == Decimal("4")
    assert Decimal(str(count.difference)) == Decimal("0")


def _assert_returns(returns: list, originals: list) -> None:
    original_companies = {row.name: row.company for row in originals}
    assert returns
    for credit in returns:
        assert credit.company == original_companies[credit.return_against]


def _assert_sales_order_links(sales_order: str, invoices: list) -> None:
    source_company = frappe.db.get_value("Sales Order", sales_order, "company")
    for invoice in invoices:
        assert invoice.items
        if invoice.company == source_company:
            assert all(row.sales_order == sales_order and row.so_detail for row in invoice.items)
        else:
            assert all(not row.sales_order and not row.so_detail for row in invoice.items)
            assert invoice.ua_sale_fulfillment


def _evidence(checkout_name: str, invoices: list) -> dict:
    routes = json.loads(frappe.db.get_value("GSF Checkout", checkout_name, "route_manifest"))
    return {
        "fulfillment": checkout_name,
        "routes": [
            {
                "provider": row["provider_id"],
                "seller": row["seller_company"],
                "fiscal_route": row["fiscal_route"],
            }
            for row in routes
        ],
        "invoices": [row.name for row in invoices],
    }
