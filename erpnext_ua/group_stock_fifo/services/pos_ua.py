"""Adapter between the POS-UA customer saga and GSF stock services (ADR-015)."""

from __future__ import annotations

import json
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import frappe

from ..setup.layer_dimension import ALLOCATION_FIELD, MANAGED_SALE_FIELD
from .domain import OWN_POOL_ROLE, GSFError
from .layers import gsf_enabled
from .pos_return_domain import ReturnLine, consume_return_rows
from .returns import accept_return, returned_qty
from .serial_identity import single_serial


@dataclass(frozen=True, slots=True)
class POSScope:
    company_group: str
    physical_location: str
    company: str


def scope_for_desk(desk: Any) -> POSScope | None:
    """Return the GSF scope only for an explicitly bound POS warehouse."""
    if not gsf_enabled():
        return None
    binding = frappe.db.get_value(
        "GSF Warehouse Binding",
        {
            "warehouse": desk.warehouse,
            "manager_app": "GSF",
            "warehouse_role": OWN_POOL_ROLE,
            "enabled": 1,
        },
        ["company_group", "physical_location", "company"],
        as_dict=True,
    )
    if not binding:
        return None
    if binding.company != desk.company:
        raise GSFError(
            f"POS desk {desk.name} belongs to {desk.company}, but {desk.warehouse} belongs to {binding.company}",
            "WAREHOUSE_DOMAIN_CONFLICT",
        )
    can_sell = frappe.db.exists(
        "GSF Location Company Binding",
        {
            "company_group": binding.company_group,
            "physical_location": binding.physical_location,
            "company": binding.company,
            "enabled": 1,
            "can_sell": 1,
        },
    )
    if not can_sell:
        raise GSFError(f"{binding.company} cannot sell in this GSF location", "COMPANY_NOT_GROUP_MEMBER")
    return POSScope(binding.company_group, binding.physical_location, binding.company)


def post_sale(order: Any, desk: Any) -> Any:
    """Run a paid POS sale through the persistent global-FIFO checkout."""
    from .checkout import CheckoutLine, CheckoutRequest, open_checkout, run

    scope = scope_for_desk(desk)
    if not scope:
        raise GSFError(f"POS desk {desk.name} is not GSF-bound", "WAREHOUSE_BINDING_MISSING")
    _validate_stock_uoms(order)
    checkout = open_checkout(
        CheckoutRequest(
            idempotency_key=f"UA_POS:{order.name}",
            company_group=scope.company_group,
            physical_location=scope.physical_location,
            seller_company=scope.company,
            customer=order.customer,
            external_order_doctype="POS Order",
            external_order_name=order.name,
            sales_channel="POS-UA",
            currency=frappe.db.get_value("Company", scope.company, "default_currency"),
            conversion_rate=Decimal("1"),
            requires_fiscalization=order.fiscal_mode == "Fiscal",
            lines=tuple(
                CheckoutLine(
                    item_code=row.item_code,
                    qty=Decimal(str(row.qty)),
                    rate=Decimal(str(row.rate)),
                    external_row_id=row.name,
                    uom=row.uom,
                    barcode=row.barcode,
                    serial_no=single_serial(row.serial_no),
                    batch_no=row.batch_no,
                    discount_amount=Decimal(str(row.discount_amount or 0)),
                )
                for row in order.items
            ),
        )
    )
    checkout = run(checkout.name)
    if not checkout.sales_invoice:
        raise GSFError(
            f"GSF checkout {checkout.name} stopped at {checkout.status} without a Sales Invoice",
            checkout.failure_code or "MANUAL_REVIEW_REQUIRED",
        )
    order.gsf_checkout = checkout.name
    order.sales_invoices_json = checkout.sales_invoices
    return frappe.get_doc("Sales Invoice", checkout.sales_invoice)


def post_return(order: Any, desk: Any) -> Any:
    """Return exact GSF and CC technical rows from every invoice on the receipt."""
    original_order = frappe.get_doc("POS Order", order.return_against)
    scope = scope_for_desk(desk)
    if not scope:
        raise GSFError(f"Return desk {desk.name} is not GSF-bound", "WAREHOUSE_BINDING_MISSING")
    if not original_order.gsf_checkout:
        raise GSFError("The original POS receipt has no Sale Fulfillment", "MANUAL_REVIEW_REQUIRED")
    planned = _plan_fulfillment_return(original_order, order)
    if not planned:
        raise GSFError("The return has no exact sold rows", "MANUAL_REVIEW_REQUIRED")
    from .fulfillment_payments import split_pos_payments
    from .fulfillment_pos_routes import posted_pos_routes

    routes = {route.invoice.name: route for route in posted_pos_routes(original_order, desk)}
    totals = OrderedDict(
        (routes[invoice_name].route.stable_id, _return_total(invoice_name, lines))
        for invoice_name, lines in planned.items()
    )
    from erpnext_ua.ua_gift_certificates.services.fulfillment import (
        return_route_payment_components,
    )

    payment_plan = split_pos_payments(
        order,
        totals,
        is_return=True,
        fixed_route_payments=return_route_payment_components(
            order,
            {
                invoice_name: routes[invoice_name].route.stable_id
                for invoice_name in planned
            },
        ),
    )
    returns = []
    for invoice_name, lines in planned.items():
        route = routes[invoice_name]
        values = _route_return_invoice_values(
            order,
            route=route,
            payments=payment_plan[route.route.stable_id],
        )
        if route.route.provider_id == "GSF":
            invoice = accept_return(
                sales_invoice=invoice_name,
                lines=lines,
                invoice_values=values,
            )
        elif route.route.provider_id == "CC":
            invoice = _post_cc_return(order, invoice_name, lines, values)
        else:
            raise GSFError(
                f"Route {route.route.provider_id} cannot return managed stock",
                "MANUAL_REVIEW_REQUIRED",
            )
        returns.append(invoice)
    order.sales_invoices_json = json.dumps([invoice.name for invoice in returns], separators=(",", ":"))
    return returns[0]


def sale_invoice_values(pos_order: str) -> dict[str, Any]:
    return _invoice_values(frappe.get_doc("POS Order", pos_order), is_return=False)


def return_invoice_values(order: Any) -> dict[str, Any]:
    return _invoice_values(order, is_return=True)


def _invoice_values(order: Any, *, is_return: bool) -> dict[str, Any]:
    from erpnext_ua.ua_gift_certificates.adapters.accounting import invoice_payments

    payments = invoice_payments(order, is_return=is_return)
    return {
        "is_pos": 1,
        "ua_pos_order": order.name,
        "ua_pos_desk": order.cash_desk,
        "ua_pos_shift": order.operational_shift,
        "payments": payments,
        "change_amount": order.change_amount,
    }


def is_gsf_return(order: Any) -> bool:
    if order.order_type != "Return" or not order.return_against:
        return False
    return bool(frappe.db.get_value("POS Order", order.return_against, "gsf_checkout"))


def record_fiscal_result(
    order: Any,
    *,
    state: str,
    receipt: str | None = None,
    receipts: list[str] | None = None,
) -> None:
    if not order.gsf_checkout:
        return
    from .checkout import record_fiscal_result as record_checkout_fiscal_result

    record_checkout_fiscal_result(
        order.gsf_checkout,
        fiscal_state=state,
        prro_receipt=receipt,
        prro_receipts=receipts,
    )


def _plan_return_lines(original_order: Any, original: Any, return_order: Any) -> list[ReturnLine]:
    invoice_rows = _invoice_rows_by_pos_item(original)
    all_rows = {row.name for rows in invoice_rows.values() for row in rows}
    prior = returned_qty(original.name, all_rows)
    return consume_return_rows(invoice_rows, return_order.items, prior)


def _plan_fulfillment_return(original_order: Any, return_order: Any) -> OrderedDict[str, list[ReturnLine]]:
    from erpnext_ua.consignment_and_commission.setup.ownership_dimension import (
        ALLOCATION_FIELD as CC_ALLOCATION_FIELD,
    )

    from .fulfillment_reservation import checkout_refs

    checkout = frappe.get_doc("GSF Checkout", original_order.gsf_checkout)
    invoice_names = json.loads(original_order.sales_invoices_json or "[]")
    if not invoice_names and original_order.sales_invoice:
        invoice_names = [original_order.sales_invoice]
    invoices = {name: frappe.get_doc("Sales Invoice", name) for name in invoice_names}
    invoices_by_route = {
        invoice.get("ua_fulfillment_route"): invoice for invoice in invoices.values()
    }
    grouped: dict[str, list[Any]] = defaultdict(list)
    row_invoice: dict[str, str] = {}
    for ref in checkout_refs(checkout):
        invoice = invoices_by_route.get(ref.route.stable_id)
        if not invoice:
            raise GSFError(
                f"Fulfillment route {ref.route.stable_id} has no Sales Invoice",
                "MANUAL_REVIEW_REQUIRED",
            )
        fieldname = ALLOCATION_FIELD if ref.route.provider_id == "GSF" else CC_ALLOCATION_FIELD
        rows = [row for row in invoice.items if row.get(fieldname) == ref.allocation_name]
        if not rows:
            raise GSFError(
                f"Allocation {ref.allocation_name} has no sold invoice rows",
                "MANUAL_REVIEW_REQUIRED",
            )
        for row in sorted(rows, key=lambda value: value.idx):
            grouped[ref.external_row_id].append(row)
            row_invoice[row.name] = invoice.name

    all_rows = {row.name for rows in grouped.values() for row in rows}
    prior = _fulfillment_returned_qty(invoices, all_rows)
    lines = consume_return_rows(grouped, return_order.items, prior)
    result: OrderedDict[str, list[ReturnLine]] = OrderedDict()
    for line in lines:
        result.setdefault(row_invoice[line.sales_invoice_item], []).append(line)
    return result


def _fulfillment_returned_qty(
    invoices: dict[str, Any],
    invoice_rows: set[str],
) -> dict[str, Decimal]:
    from erpnext_ua.consignment_and_commission.setup.ownership_dimension import (
        MANAGED_SALE_FIELD as CC_MANAGED_SALE_FIELD,
    )

    result = {name: Decimal("0") for name in invoice_rows}
    for invoice in invoices.values():
        names = {row.name for row in invoice.items if row.name in invoice_rows}
        if invoice.get(MANAGED_SALE_FIELD):
            result.update(returned_qty(invoice.name, names))
        elif invoice.get(CC_MANAGED_SALE_FIELD):
            for row in frappe.get_all(
                "CC Sale Allocation",
                filters={"sales_invoice_item": ("in", list(names))},
                fields=["sales_invoice_item", "returned_qty"],
            ):
                result[row.sales_invoice_item] = Decimal(str(row.returned_qty or 0))
        else:
            raise GSFError(
                f"Sales Invoice {invoice.name} is not allocation-managed",
                "MANUAL_REVIEW_REQUIRED",
            )
    return result


def _return_total(invoice_name: str, lines: list[ReturnLine]) -> Decimal:
    invoice = frappe.get_doc("Sales Invoice", invoice_name)
    rows = {row.name: row for row in invoice.items}
    total = Decimal("0")
    for line in lines:
        row = rows[line.sales_invoice_item]
        sold_qty = abs(Decimal(str(row.qty or 0)))
        total += abs(Decimal(str(row.net_amount or 0))) * line.qty / sold_qty
    return total


def _route_return_invoice_values(order: Any, *, route: Any, payments: list[dict]) -> dict[str, Any]:
    values = {
        "is_pos": 1,
        "ua_pos_order": order.name,
        "ua_pos_desk": route.cash_desk,
        "ua_pos_shift": route.operational_shift,
        "payments": payments,
        "change_amount": 0,
        "ua_sale_fulfillment": frappe.db.get_value(
            "POS Order", order.return_against, "gsf_checkout"
        ),
        "ua_fulfillment_route": route.route.stable_id,
        "remarks": f"Exact return for POS Order {order.return_against}",
    }
    if route.route.legal_entity_type == "FOP Profile":
        values["ua_fop_profile"] = route.route.legal_entity_name
    return values


def _post_cc_return(
    order: Any,
    invoice_name: str,
    lines: list[ReturnLine],
    invoice_values: dict[str, Any],
) -> Any:
    from frappe.utils import getdate

    from erpnext_ua.consignment_and_commission.integrations.sale_returns import (
        create_return_invoice,
    )
    from erpnext_ua.consignment_and_commission.services.sale_return import (
        ManagedReturnLine,
        ManagedReturnRequest,
    )
    sale_allocations = {
        row.sales_invoice_item: row.name
        for row in frappe.get_all(
            "CC Sale Allocation",
            filters={
                "sales_invoice": invoice_name,
                "sales_invoice_item": ("in", [line.sales_invoice_item for line in lines]),
            },
            fields=["name", "sales_invoice_item"],
        )
    }
    if len(sale_allocations) != len(lines):
        raise GSFError(
            f"Sales Invoice {invoice_name} has an incomplete CC sale trail",
            "MANUAL_REVIEW_REQUIRED",
        )
    invoice = create_return_invoice(
        ManagedReturnRequest(
            idempotency_key=f"POS_RETURN:{order.name}:{invoice_name}",
            posting_date=getdate(),
            lines=tuple(
                ManagedReturnLine(sale_allocations[line.sales_invoice_item], line.qty)
                for line in lines
            ),
        ),
        invoice_values=invoice_values,
        allow_transaction_writes=True,
    )
    if invoice.docstatus == 0:
        invoice.flags.ignore_permissions = True
        invoice.submit()
    return invoice


def _validate_stock_uoms(order: Any) -> None:
    """Validate stock UOM and the tracked identity before reserving anything."""
    item_codes = {row.item_code for row in order.items}
    items = {
        row.name: row
        for row in frappe.get_all(
            "Item",
            filters={"name": ("in", list(item_codes))},
            fields=["name", "stock_uom", "has_serial_no", "has_batch_no"],
        )
    }
    seen_serials: set[str] = set()
    for row in order.items:
        item = items.get(row.item_code)
        if not item:
            raise GSFError(f"Unknown item {row.item_code}", "MANUAL_REVIEW_REQUIRED")
        if row.uom != item.stock_uom:
            raise GSFError(
                f"POS row {row.name} uses {row.uom}; GSF requires stock UOM {item.stock_uom}",
                "MANUAL_REVIEW_REQUIRED",
            )
        serial_no = single_serial(row.serial_no)
        if item.has_serial_no:
            if not serial_no:
                raise GSFError(
                    f"POS row {row.name} requires a scanned Serial No",
                    "SERIAL_AMBIGUOUS",
                )
            if Decimal(str(row.qty)) != Decimal("1"):
                raise GSFError(
                    f"POS row {row.name} must contain one unit for Serial No {serial_no}",
                    "SERIAL_AMBIGUOUS",
                )
            if serial_no in seen_serials:
                raise GSFError(
                    f"Serial No {serial_no} appears more than once in the basket",
                    "SERIAL_AMBIGUOUS",
                )
            seen_serials.add(serial_no)
        elif serial_no:
            raise GSFError(
                f"POS row {row.name} supplies Serial No {serial_no} for an untracked item",
                "SERIAL_AMBIGUOUS",
            )
        if not item.has_batch_no and row.batch_no:
            raise GSFError(
                f"POS row {row.name} supplies Batch {row.batch_no} for a non-batch item",
                "BATCH_MISMATCH",
            )


def _invoice_rows_by_pos_item(original: Any) -> dict[str, list[Any]]:
    allocation_names = {row.get(ALLOCATION_FIELD) for row in original.items if row.get(ALLOCATION_FIELD)}
    if not allocation_names:
        raise GSFError(f"GSF sale {original.name} has no allocations", "MANUAL_REVIEW_REQUIRED")
    external_rows = {
        row.name: row.external_row_id
        for row in frappe.get_all(
            "GSF Allocation",
            filters={"name": ("in", list(allocation_names))},
            fields=["name", "external_row_id"],
        )
    }
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in sorted(original.items, key=lambda item: item.idx):
        external_row = external_rows.get(row.get(ALLOCATION_FIELD))
        if not external_row:
            raise GSFError(f"Invoice row {row.name} has no POS row identity", "MANUAL_REVIEW_REQUIRED")
        grouped[external_row].append(row)
    return grouped
