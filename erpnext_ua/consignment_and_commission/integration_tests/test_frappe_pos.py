"""Persistent split POS route, retry, print-state and compensation lifecycle."""

from __future__ import annotations

from decimal import Decimal

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import getdate, nowdate

from erpnext_ua.consignment_and_commission.integrations.pos import (
    advance_pos_route,
    compensate_pos_checkout,
    mark_print_job_failed,
    mark_print_job_succeeded,
    prepare_pos_checkout,
)
from erpnext_ua.consignment_and_commission.integrations.reporting import (
    get_pos_queue,
)
from erpnext_ua.consignment_and_commission.integrations.reservations import (
    reserve_stock,
)
from erpnext_ua.consignment_and_commission.services.pos_checkout import (
    POSCheckoutRequest,
    POSRouteLine,
    POSRouteRequest,
)
from erpnext_ua.consignment_and_commission.services.pos_saga import (
    PaymentTender,
)
from erpnext_ua.consignment_and_commission.services.reservation import (
    ReservationRequest,
)
from erpnext_ua.consignment_and_commission.setup.ownership_dimension import (
    POS_CHECKOUT_FIELD,
    POS_ORDER_FIELD,
    POS_ROUTE_FIELD,
)

from .test_frappe_foundation import (
    COMPANY,
    LOCATION,
    PARTNER,
    _cleanup_integration_records,
)
from .test_frappe_pricing import _cleanup_price_versions
from .test_frappe_receipt import (
    _cleanup_off_balance_records,
    _cleanup_receipt_records,
    _ensure_item,
    _ensure_receipt_context,
)
from .test_frappe_sale import (
    _cleanup_managed_sales,
    _ensure_account_mapping,
    _ensure_customer,
)

MODE_OF_PAYMENT = "_CC Integration Cash"
_created_mode_of_payment = False


def _ensure_mode_of_payment() -> str:
    global _created_mode_of_payment

    if frappe.db.exists("Mode of Payment", MODE_OF_PAYMENT):
        return MODE_OF_PAYMENT
    name = frappe.get_doc(
        {
            "doctype": "Mode of Payment",
            "mode_of_payment": MODE_OF_PAYMENT,
            "type": "Cash",
            "enabled": 1,
        }
    ).insert(ignore_permissions=True).name
    _created_mode_of_payment = True
    return name


def _cleanup_pos_records() -> None:
    global _created_mode_of_payment

    if not frappe.db.exists("DocType", "CC POS Checkout"):
        return
    routes = frappe.get_all(
        "CC POS Route",
        filters={"company": COMPANY},
        fields=["name", "sales_invoice", "print_job"],
    )
    for row in routes:
        if row.sales_invoice and frappe.db.exists("Sales Invoice", row.sales_invoice):
            invoice = frappe.get_doc("Sales Invoice", row.sales_invoice)
            if invoice.docstatus == 1:
                invoice.cancel()
    from ..doctype.cc_pos_checkout.cc_pos_checkout import (
        TEST_CLEANUP_FLAG as CHECKOUT_CLEANUP_FLAG,
    )
    from ..doctype.cc_pos_print_job.cc_pos_print_job import (
        TEST_CLEANUP_FLAG as PRINT_CLEANUP_FLAG,
    )
    from ..doctype.cc_pos_route.cc_pos_route import TEST_CLEANUP_FLAG as ROUTE_CLEANUP_FLAG

    flags = (CHECKOUT_CLEANUP_FLAG, PRINT_CLEANUP_FLAG, ROUTE_CLEANUP_FLAG)
    previous = {flag: getattr(frappe.flags, flag, False) for flag in flags}
    for flag in flags:
        setattr(frappe.flags, flag, True)
    try:
        for row in routes:
            if row.print_job and frappe.db.exists("CC POS Print Job", row.print_job):
                frappe.delete_doc(
                    "CC POS Print Job",
                    row.print_job,
                    force=True,
                    ignore_permissions=True,
                )
        for row in routes:
            if frappe.db.exists("CC POS Route", row.name):
                frappe.delete_doc(
                    "CC POS Route",
                    row.name,
                    force=True,
                    ignore_permissions=True,
                )
        for name in frappe.get_all(
            "CC POS Checkout",
            filters={"external_order_name": ("like", "_CC-POS-%")},
            pluck="name",
        ):
            frappe.delete_doc(
                "CC POS Checkout",
                name,
                force=True,
                ignore_permissions=True,
            )
    finally:
        for flag, value in previous.items():
            setattr(frappe.flags, flag, value)
    if _created_mode_of_payment and frappe.db.exists("Mode of Payment", MODE_OF_PAYMENT):
        frappe.delete_doc(
            "Mode of Payment",
            MODE_OF_PAYMENT,
            force=True,
            ignore_permissions=True,
        )
        _created_mode_of_payment = False
    frappe.db.commit()


def _cleanup_all() -> None:
    _cleanup_off_balance_records()
    _cleanup_pos_records()
    _cleanup_managed_sales()
    _cleanup_price_versions()
    _cleanup_receipt_records()
    _cleanup_integration_records()


class TestFrappePOS(IntegrationTestCase):
    def test_split_routes_persist_submit_and_compensate_in_reverse(self) -> None:
        _cleanup_all()
        self.addCleanup(_cleanup_all)
        company, warehouses, commission_contract = _ensure_receipt_context()
        _ensure_account_mapping(company)
        customer = _ensure_customer()
        item_code = _ensure_item()
        consignment_contract = frappe.get_doc(
            {
                "doctype": "CC Contract",
                "contract_title": "_CC POS Consignment Contract",
                "status": "ACTIVE",
                "partner_profile": PARTNER,
                "company": COMPANY,
                "location": LOCATION,
                "relationship_model": "CONSIGNMENT",
                "currency": company.default_currency,
                "commission_rate": 0,
                "valid_from": nowdate(),
                "settlement_frequency": "MONTHLY",
                "settlement_deadline_days": 7,
                "fiscal_policy": "AUTO",
                "price_authority": "CONTRACT",
            }
        ).insert(ignore_permissions=True)
        receipts = []
        for contract, posting_time in (
            (commission_contract, "00:01:00"),
            (consignment_contract.name, "00:02:00"),
        ):
            receipt = frappe.get_doc(
                {
                    "doctype": "CC Receipt",
                    "posting_date": nowdate(),
                    "posting_time": posting_time,
                    "contract": contract,
                    "items": [
                        {
                            "item_code": item_code,
                            "qty": 1,
                            "uom": "Nos",
                            "conversion_factor": 1,
                            "accounting_unit_value": 100,
                        }
                    ],
                }
            ).insert(ignore_permissions=True)
            receipt.submit()
            receipts.append(receipt)
        consignment_lot = receipts[1].items[0].stock_lot
        price_start = frappe.db.get_value(
            "CC Stock Lot",
            consignment_lot,
            "received_datetime",
        )
        price = frappe.get_doc(
            {
                "doctype": "CC Price Version",
                "stock_lot": consignment_lot,
                "partner_rate": 70,
                "valid_from": price_start,
            }
        ).insert(ignore_permissions=True)
        price.submit()
        frappe.db.commit()

        allocations = {}
        for model, warehouse in (
            ("COMMISSION", warehouses["COMMISSION"]),
            ("CONSIGNMENT", warehouses["CONSIGNMENT"]),
        ):
            allocation = reserve_stock(
                ReservationRequest(
                    idempotency_key=f"_CC-POS-RESERVE-{model}",
                    item_code=item_code,
                    company=company.name,
                    location=LOCATION,
                    qty=Decimal("1"),
                    allowed_warehouses=frozenset({warehouse}),
                )
            )
            allocations[model] = allocation
            frappe.db.commit()

        location = frappe.get_doc("CC Location", LOCATION)
        mode_of_payment = _ensure_mode_of_payment()
        frappe.db.commit()
        checkout = prepare_pos_checkout(
            POSCheckoutRequest(
                idempotency_key="_CC-POS-CHECKOUT-1",
                external_order_doctype="POS Order",
                external_order_name="_CC-POS-ORDER-1",
                lookup_token="_CC-POS-LOOKUP-1",
                customer=customer,
                posting_date=getdate(nowdate()),
                currency=company.default_currency,
                conversion_rate=Decimal("1"),
                fiscal_checkout=True,
                routes=(
                    POSRouteRequest(
                        group_id="_CC-POS-COMMISSION-NON-FISCAL",
                        company=company.name,
                        location=LOCATION,
                        legal_entity_type=location.legal_entity_type,
                        legal_entity_name=location.legal_entity_name,
                        fiscal_route="NON_FISCAL",
                        lines=(
                            POSRouteLine(
                                allocations["COMMISSION"].name,
                                Decimal("100"),
                                "ROW-COMMISSION",
                            ),
                        ),
                    ),
                    POSRouteRequest(
                        group_id="_CC-POS-CONSIGNMENT-FISCAL",
                        company=company.name,
                        location=LOCATION,
                        legal_entity_type=location.legal_entity_type,
                        legal_entity_name=location.legal_entity_name,
                        fiscal_route="FISCAL",
                        lines=(
                            POSRouteLine(
                                allocations["CONSIGNMENT"].name,
                                Decimal("100"),
                                "ROW-CONSIGNMENT",
                            ),
                        ),
                    ),
                ),
                tenders=(
                    PaymentTender("_CC-POS-CASH", mode_of_payment, Decimal("200")),
                ),
            )
        )
        self.assertEqual(checkout.status, "PLANNED")
        self.assertEqual(Decimal(str(checkout.total_amount)), Decimal("200.0"))
        routes = frappe.get_all(
            "CC POS Route",
            filters={"checkout": checkout.name},
            fields=["name", "group_id", "fiscal_route", "total_amount"],
            order_by="group_id asc",
        )
        self.assertEqual({row.fiscal_route for row in routes}, {"FISCAL", "NON_FISCAL"})
        self.assertEqual(
            sum((Decimal(str(row.total_amount)) for row in routes), Decimal("0")),
            Decimal("200.0"),
        )
        frappe.db.commit()

        submitted = []
        for route_row in routes:
            route = advance_pos_route(route_row.name)
            self.assertEqual(route.status, "PRINT_PENDING")
            invoice = frappe.get_doc("Sales Invoice", route.sales_invoice)
            self.assertEqual(invoice.docstatus, 1)
            self.assertEqual(invoice.get(POS_CHECKOUT_FIELD), checkout.name)
            self.assertEqual(invoice.get(POS_ROUTE_FIELD), route.name)
            self.assertEqual(invoice.get(POS_ORDER_FIELD), checkout.external_order_name)
            submitted.append((route, invoice))
            frappe.db.commit()

        failed_job = mark_print_job_failed(
            submitted[0][0].print_job,
            error="Retryable printer outage",
        )
        self.assertEqual(failed_job.status, "FAILED")
        self.assertEqual(failed_job.attempts, 1)
        queue = get_pos_queue({"company": company.name, "exceptions_only": 1})
        self.assertEqual({row.route for row in queue}, {row[0].name for row in submitted})
        self.assertIn("FAILED", {row.print_status for row in queue})
        frappe.db.commit()

        compensated = compensate_pos_checkout(
            checkout.name,
            reason="Integration compensation proof",
        )
        self.assertEqual(compensated.status, "COMPENSATED")
        for route, invoice in submitted:
            route.reload()
            invoice.reload()
            self.assertEqual(route.status, "COMPENSATED")
            self.assertEqual(invoice.docstatus, 2)
            self.assertEqual(
                frappe.db.get_value("CC POS Print Job", route.print_job, "status"),
                "CANCELLED",
            )
        self.assertEqual(
            {
                frappe.db.get_value("CC Allocation", allocation.name, "status")
                for allocation in allocations.values()
            },
            {"CONSUMED"},
        )

        completed_allocations = {}
        for model, warehouse in (
            ("COMMISSION", warehouses["COMMISSION"]),
            ("CONSIGNMENT", warehouses["CONSIGNMENT"]),
        ):
            frappe.db.commit()
            completed_allocations[model] = reserve_stock(
                ReservationRequest(
                    idempotency_key=f"_CC-POS-COMPLETE-RESERVE-{model}",
                    item_code=item_code,
                    company=company.name,
                    location=LOCATION,
                    qty=Decimal("1"),
                    allowed_warehouses=frozenset({warehouse}),
                )
            )
        frappe.db.commit()
        completed_checkout = prepare_pos_checkout(
            POSCheckoutRequest(
                idempotency_key="_CC-POS-CHECKOUT-2",
                external_order_doctype="POS Order",
                external_order_name="_CC-POS-ORDER-2",
                customer=customer,
                posting_date=getdate(nowdate()),
                currency=company.default_currency,
                conversion_rate=Decimal("1"),
                fiscal_checkout=True,
                routes=(
                    POSRouteRequest(
                        group_id="_CC-POS-COMMISSION-NON-FISCAL",
                        company=company.name,
                        location=LOCATION,
                        legal_entity_type=location.legal_entity_type,
                        legal_entity_name=location.legal_entity_name,
                        fiscal_route="NON_FISCAL",
                        lines=(
                            POSRouteLine(
                                completed_allocations["COMMISSION"].name,
                                Decimal("100"),
                            ),
                        ),
                    ),
                    POSRouteRequest(
                        group_id="_CC-POS-CONSIGNMENT-FISCAL",
                        company=company.name,
                        location=LOCATION,
                        legal_entity_type=location.legal_entity_type,
                        legal_entity_name=location.legal_entity_name,
                        fiscal_route="FISCAL",
                        lines=(
                            POSRouteLine(
                                completed_allocations["CONSIGNMENT"].name,
                                Decimal("100"),
                            ),
                        ),
                    ),
                ),
                tenders=(
                    PaymentTender("_CC-POS-CASH-2", mode_of_payment, Decimal("200")),
                ),
            )
        )
        frappe.db.commit()
        completed_routes = frappe.get_all(
            "CC POS Route",
            filters={"checkout": completed_checkout.name},
            pluck="name",
            order_by="name asc",
        )
        completed_invoice_names = []
        for route_name in completed_routes:
            route = advance_pos_route(route_name)
            completed_invoice_names.append(route.sales_invoice)
            replay = advance_pos_route(route_name)
            self.assertEqual(replay.sales_invoice, route.sales_invoice)
            job = mark_print_job_succeeded(
                route.print_job,
                provider_reference=f"PRINTED-{route.name}",
            )
            self.assertEqual(job.attempts, 1)
            frappe.db.commit()
        completed_checkout.reload()
        self.assertEqual(completed_checkout.status, "COMPLETED")
        manual = compensate_pos_checkout(
            completed_checkout.name,
            reason="Printed fiscal route requires external reversal",
        )
        self.assertEqual(manual.status, "MANUAL_REVIEW")
        self.assertEqual(
            {
                frappe.db.get_value("Sales Invoice", name, "docstatus")
                for name in completed_invoice_names
            },
            {1},
        )
