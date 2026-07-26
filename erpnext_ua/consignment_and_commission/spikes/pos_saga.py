"""Test-site-only POS split, retry, compensation, print and return probes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from ..services.pos_saga import (
    CartLine,
    CheckoutGroup,
    PaymentAllocation,
    PaymentTender,
    ReturnSource,
    RouteProgress,
    allocate_payment_plan,
    plan_compensation,
    plan_return,
    split_checkout,
)
from .fifo import _ensure_location_warehouses
from .inventory_dimension import (
    DIMENSION_FIELD,
    ITEM_CODE,
    _assert_test_scope,
    _dimension_balance,
    _ensure_customer,
    _ensure_dimension,
    _ensure_item,
    _ensure_lot_value,
    _ensure_reference_doctype,
    _ledger_evidence,
    _make_stock_entry,
)

ROUTE_DOCTYPE = "TP Spike POS Route"
PRINT_JOB_DOCTYPE = "TP Spike POS Print Job"
RESERVATION_DOCTYPE = "TP Spike POS Reservation"

RELATIONSHIP_FIELD = "tp_spike_relationship_model"
LEGAL_ENTITY_FIELD = "tp_spike_legal_entity"
FISCAL_ROUTE_FIELD = "tp_spike_fiscal_route"
GROUP_FIELD = "tp_spike_split_group_id"
IDEMPOTENCY_FIELD = "tp_spike_idempotency_key"

LEGAL_ENTITIES = {"A": "TP-LEGAL-ENTITY-A", "B": "TP-LEGAL-ENTITY-B"}


class SimulatedTimeout(RuntimeError):
    """Raised after a committed route document to prove retry idempotency."""


def _ensure_saga_doctypes(frappe: Any) -> None:
    definitions = {
        ROUTE_DOCTYPE: {
            "autoname": "field:route_key",
            "naming_rule": "By fieldname",
            "fields": [
                {
                    "fieldname": "route_key",
                    "label": "Route Key",
                    "fieldtype": "Data",
                    "reqd": 1,
                    "unique": 1,
                },
                {
                    "fieldname": "pos_order",
                    "label": "POS Order",
                    "fieldtype": "Link",
                    "options": "POS Order",
                    "reqd": 1,
                },
                {
                    "fieldname": "split_group_id",
                    "label": "Split Group ID",
                    "fieldtype": "Data",
                    "reqd": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": "company",
                    "label": "Company",
                    "fieldtype": "Link",
                    "options": "Company",
                    "reqd": 1,
                },
                {
                    "fieldname": "legal_entity",
                    "label": "Legal Entity",
                    "fieldtype": "Data",
                    "reqd": 1,
                },
                {
                    "fieldname": "fiscal_route",
                    "label": "Fiscal Route",
                    "fieldtype": "Select",
                    "options": "FISCAL\nNON_FISCAL",
                    "reqd": 1,
                },
                {
                    "fieldname": "status",
                    "label": "Status",
                    "fieldtype": "Select",
                    "options": "PLANNED\nDRAFT\nSUBMITTED\nCOMPLETED\nCOMPENSATED",
                    "reqd": 1,
                },
                {
                    "fieldname": "sales_invoice",
                    "label": "Sales Invoice",
                    "fieldtype": "Link",
                    "options": "Sales Invoice",
                },
                {
                    "fieldname": "print_job",
                    "label": "Print Job",
                    "fieldtype": "Link",
                    "options": PRINT_JOB_DOCTYPE,
                },
                {
                    "fieldname": "failure_note",
                    "label": "Failure Note",
                    "fieldtype": "Small Text",
                },
            ],
        },
        PRINT_JOB_DOCTYPE: {
            "autoname": "field:job_key",
            "naming_rule": "By fieldname",
            "fields": [
                {
                    "fieldname": "job_key",
                    "label": "Job Key",
                    "fieldtype": "Data",
                    "reqd": 1,
                    "unique": 1,
                },
                {
                    "fieldname": "pos_order",
                    "label": "POS Order",
                    "fieldtype": "Link",
                    "options": "POS Order",
                    "reqd": 1,
                },
                {
                    "fieldname": "sales_invoice",
                    "label": "Sales Invoice",
                    "fieldtype": "Link",
                    "options": "Sales Invoice",
                    "reqd": 1,
                },
                {
                    "fieldname": "print_kind",
                    "label": "Print Kind",
                    "fieldtype": "Select",
                    "options": "FISCAL_RECEIPT\nNON_FISCAL_GOODS_RECEIPT",
                    "reqd": 1,
                },
                {
                    "fieldname": "status",
                    "label": "Status",
                    "fieldtype": "Select",
                    "options": "QUEUED\nPRINTED\nFAILED",
                    "reqd": 1,
                },
            ],
        },
        RESERVATION_DOCTYPE: {
            "autoname": "field:reservation_key",
            "naming_rule": "By fieldname",
            "fields": [
                {
                    "fieldname": "reservation_key",
                    "label": "Reservation Key",
                    "fieldtype": "Data",
                    "reqd": 1,
                    "unique": 1,
                },
                {
                    "fieldname": "pos_order",
                    "label": "POS Order",
                    "fieldtype": "Link",
                    "options": "POS Order",
                    "reqd": 1,
                },
                {
                    "fieldname": "stock_lot",
                    "label": "Stock Lot",
                    "fieldtype": "Link",
                    "options": "TP Spike Lot",
                    "reqd": 1,
                },
                {
                    "fieldname": "qty",
                    "label": "Qty",
                    "fieldtype": "Float",
                    "reqd": 1,
                },
                {
                    "fieldname": "status",
                    "label": "Status",
                    "fieldtype": "Select",
                    "options": "RESERVED\nCONSUMED\nRELEASED",
                    "reqd": 1,
                },
            ],
        },
    }
    permissions = [
        {
            "role": "System Manager",
            "read": 1,
            "write": 1,
            "create": 1,
            "delete": 1,
        }
    ]
    for doctype in (PRINT_JOB_DOCTYPE, RESERVATION_DOCTYPE, ROUTE_DOCTYPE):
        definition = definitions[doctype]
        if frappe.db.exists("DocType", doctype):
            continue
        frappe.get_doc(
            {
                "doctype": "DocType",
                "name": doctype,
                "module": "Consignment and Commission",
                "custom": 1,
                "autoname": definition["autoname"],
                "naming_rule": definition["naming_rule"],
                "fields": definition["fields"],
                "permissions": permissions,
            }
        ).insert(ignore_permissions=True)


def _ensure_custom_fields(frappe: Any) -> None:
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            "POS Order Item": [
                {
                    "fieldname": RELATIONSHIP_FIELD,
                    "label": "TP Spike Relationship Model",
                    "fieldtype": "Select",
                    "options": "OWN\nCOMMISSION\nCONSIGNMENT",
                    "read_only": 1,
                },
                {
                    "fieldname": LEGAL_ENTITY_FIELD,
                    "label": "TP Spike Legal Entity",
                    "fieldtype": "Data",
                    "read_only": 1,
                },
                {
                    "fieldname": FISCAL_ROUTE_FIELD,
                    "label": "TP Spike Fiscal Route",
                    "fieldtype": "Select",
                    "options": "FISCAL\nNON_FISCAL",
                    "read_only": 1,
                },
            ],
            "Sales Invoice": [
                {
                    "fieldname": GROUP_FIELD,
                    "label": "TP Spike Split Group ID",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": LEGAL_ENTITY_FIELD,
                    "label": "TP Spike Legal Entity",
                    "fieldtype": "Data",
                    "read_only": 1,
                },
                {
                    "fieldname": FISCAL_ROUTE_FIELD,
                    "label": "TP Spike Fiscal Route",
                    "fieldtype": "Select",
                    "options": "FISCAL\nNON_FISCAL",
                    "read_only": 1,
                },
                {
                    "fieldname": IDEMPOTENCY_FIELD,
                    "label": "TP Spike Idempotency Key",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "search_index": 1,
                },
            ],
            "Sales Invoice Item": [
                {
                    "fieldname": RELATIONSHIP_FIELD,
                    "label": "TP Spike Relationship Model",
                    "fieldtype": "Select",
                    "options": "OWN\nCOMMISSION\nCONSIGNMENT",
                    "read_only": 1,
                },
                {
                    "fieldname": LEGAL_ENTITY_FIELD,
                    "label": "TP Spike Legal Entity",
                    "fieldtype": "Data",
                    "read_only": 1,
                },
                {
                    "fieldname": FISCAL_ROUTE_FIELD,
                    "label": "TP Spike Fiscal Route",
                    "fieldtype": "Select",
                    "options": "FISCAL\nNON_FISCAL",
                    "read_only": 1,
                },
            ],
        }
    )


def _pos_context(frappe: Any, company: str) -> dict[str, str]:
    desk = frappe.db.get_value(
        "POS Cash Desk",
        {"company": company, "status": "Active"},
        ["name", "warehouse", "default_customer"],
        as_dict=True,
    )
    if not desk:
        raise RuntimeError(f"No active POS Cash Desk found for {company!r}")
    shift = frappe.db.get_value(
        "POS Operational Shift",
        {"cash_desk": desk.name, "status": "Open"},
        ["name", "responsible_employee"],
        as_dict=True,
    )
    if not shift:
        raise RuntimeError(f"No open POS shift found for {desk.name!r}")
    return {
        "cash_desk": desk.name,
        "shift": shift.name,
        "employee": shift.responsible_employee,
        "customer": desk.default_customer,
    }


def _make_pos_order(
    frappe: Any,
    *,
    context: dict[str, str],
    run_id: str,
    lines: list[dict[str, Any]],
    total: Decimal,
    suffix: str,
    return_against: str | None = None,
) -> Any:
    order = frappe.new_doc("POS Order")
    order.cash_desk = context["cash_desk"]
    order.operational_shift = context["shift"]
    order.employee = context["employee"]
    order.customer = context["customer"]
    order.order_type = "Return" if return_against else "Sale"
    order.return_against = return_against
    order.fiscal_mode = "Fiscal"
    order.status = "Building"
    order.lookup_token = str(uuid4())
    order.idem_key = f"TP-GATE-0E-{run_id}-{suffix}"
    for line in lines:
        row = order.append(
            "items",
            {
                "item_code": line["item_code"],
                "item_name": line.get("item_name") or "TP Gate 0E Item",
                "qty": float(line["qty"]),
                "uom": "Nos",
                "rate": float(line["rate"]),
                "warehouse": line["warehouse"],
                "return_against_item": line.get("return_against_item"),
            },
        )
        row.set(DIMENSION_FIELD, line["lot_name"])
        row.set(RELATIONSHIP_FIELD, line["relationship_model"])
        row.set(LEGAL_ENTITY_FIELD, line["legal_entity"])
        row.set(FISCAL_ROUTE_FIELD, line["fiscal_route"])
    order.append(
        "payments_plan",
        {
            "mode_of_payment": "Cash",
            "kind": "Cash",
            "amount": float(total),
            "tendered_amount": float(total),
            "currency": "UAH",
            "exchange_rate": 1,
            "status": "Confirmed",
        },
    )
    order.insert(ignore_permissions=True)
    return order


def _cart_lines(order: Any, company: str) -> list[CartLine]:
    return [
        CartLine(
            row_id=row.name,
            item_code=row.item_code,
            qty=Decimal(str(row.qty)),
            rate=Decimal(str(row.rate)),
            company=company,
            legal_entity=row.get(LEGAL_ENTITY_FIELD),
            relationship_model=row.get(RELATIONSHIP_FIELD),
            warehouse=row.warehouse,
            lot_name=row.get(DIMENSION_FIELD),
            serial_no=row.serial_no,
            batch_no=row.batch_no,
        )
        for row in order.items
    ]


def _create_reservations(frappe: Any, order: Any, lines: list[CartLine]) -> list[str]:
    names = []
    for line in lines:
        reservation_key = f"{order.name}:{line.row_id}:{line.lot_name}"
        existing = frappe.db.get_value(RESERVATION_DOCTYPE, {"reservation_key": reservation_key}, "name")
        if existing:
            names.append(existing)
            continue
        reservation = frappe.get_doc(
            {
                "doctype": RESERVATION_DOCTYPE,
                "reservation_key": reservation_key,
                "pos_order": order.name,
                "stock_lot": line.lot_name,
                "qty": float(line.qty),
                "status": "RESERVED",
            }
        ).insert(ignore_permissions=True)
        names.append(reservation.name)
    return names


def _set_reservations(frappe: Any, order_name: str, status: str) -> None:
    for name in frappe.get_all(RESERVATION_DOCTYPE, filters={"pos_order": order_name}, pluck="name"):
        frappe.db.set_value(RESERVATION_DOCTYPE, name, "status", status, update_modified=False)


def _ensure_route(frappe: Any, order_name: str, group: CheckoutGroup) -> Any:
    existing = frappe.db.get_value(ROUTE_DOCTYPE, {"route_key": group.group_id}, "name")
    if existing:
        return frappe.get_doc(ROUTE_DOCTYPE, existing)
    return frappe.get_doc(
        {
            "doctype": ROUTE_DOCTYPE,
            "route_key": group.group_id,
            "pos_order": order_name,
            "split_group_id": group.group_id,
            "company": group.key.company,
            "legal_entity": group.key.legal_entity,
            "fiscal_route": group.key.fiscal_route,
            "status": "PLANNED",
        }
    ).insert(ignore_permissions=True)


def _group_payments(
    group: CheckoutGroup,
    allocations: tuple[PaymentAllocation, ...],
) -> list[dict[str, Any]]:
    return [
        {"mode_of_payment": row.mode_of_payment, "amount": float(row.amount)}
        for row in allocations
        if row.group_id == group.group_id
    ]


def _make_split_invoice(
    frappe: Any,
    *,
    order: Any,
    group: CheckoutGroup,
    allocations: tuple[PaymentAllocation, ...],
    route: Any,
) -> tuple[Any, bool]:
    if route.sales_invoice:
        invoice = frappe.get_doc("Sales Invoice", route.sales_invoice)
        if invoice.docstatus != 1:
            raise RuntimeError(f"Existing saga route {route.name!r} points to a non-submitted invoice")
        return invoice, False

    company_doc = frappe.get_cached_doc("Company", group.key.company)
    invoice = frappe.new_doc("Sales Invoice")
    invoice.company = group.key.company
    invoice.customer = order.customer
    invoice.posting_date = frappe.utils.nowdate()
    invoice.due_date = frappe.utils.nowdate()
    invoice.is_pos = 1
    invoice.update_stock = 1
    invoice.currency = company_doc.default_currency
    invoice.conversion_rate = 1
    invoice.debit_to = company_doc.default_receivable_account
    invoice.set("ua_pos_order", order.name)
    invoice.set("ua_pos_desk", order.cash_desk)
    invoice.set("ua_pos_shift", order.operational_shift)
    invoice.set(GROUP_FIELD, group.group_id)
    invoice.set(LEGAL_ENTITY_FIELD, group.key.legal_entity)
    invoice.set(FISCAL_ROUTE_FIELD, group.key.fiscal_route)
    invoice.set(IDEMPOTENCY_FIELD, f"{group.group_id}:sales-invoice")

    for line in group.lines:
        row = invoice.append(
            "items",
            {
                "item_code": line.item_code,
                "item_name": "TP Gate 0E mixed POS item",
                "description": f"Gate 0E {line.relationship_model} route",
                "warehouse": line.warehouse,
                "qty": float(line.qty),
                "uom": "Nos",
                "stock_uom": "Nos",
                "conversion_factor": 1,
                "rate": float(line.rate),
                "price_list_rate": float(line.rate),
                "income_account": company_doc.default_income_account,
                "expense_account": company_doc.default_expense_account,
                "cost_center": company_doc.cost_center,
                "allow_zero_valuation_rate": int(line.relationship_model != "OWN"),
            },
        )
        row.set(DIMENSION_FIELD, line.lot_name)
        row.set(RELATIONSHIP_FIELD, line.relationship_model)
        row.set(LEGAL_ENTITY_FIELD, line.legal_entity)
        row.set(FISCAL_ROUTE_FIELD, group.key.fiscal_route)

    for payment in _group_payments(group, allocations):
        invoice.append("payments", payment)
    invoice.set_missing_values()
    invoice.insert(ignore_permissions=True)
    invoice.submit()
    route.sales_invoice = invoice.name
    route.status = "SUBMITTED"
    route.save(ignore_permissions=True)
    return invoice, True


def _execute_checkout(
    frappe: Any,
    *,
    order: Any,
    groups: tuple[CheckoutGroup, ...],
    allocations: tuple[PaymentAllocation, ...],
    timeout_after: int | None = None,
) -> dict[str, list[str]]:
    created = []
    reused = []
    for index, group in enumerate(groups, 1):
        route = _ensure_route(frappe, order.name, group)
        invoice, was_created = _make_split_invoice(
            frappe,
            order=order,
            group=group,
            allocations=allocations,
            route=route,
        )
        (created if was_created else reused).append(invoice.name)
        if timeout_after and index == timeout_after:
            frappe.db.commit()
            raise SimulatedTimeout(f"Simulated timeout after committed route {group.group_id}")
    return {"created": created, "reused": reused}


def _queue_print_job(frappe: Any, order_name: str, group: CheckoutGroup, invoice_name: str) -> tuple[Any, bool]:
    job_key = f"{group.group_id}:print:{group.print_kind}"
    existing = frappe.db.get_value(PRINT_JOB_DOCTYPE, {"job_key": job_key}, "name")
    if existing:
        return frappe.get_doc(PRINT_JOB_DOCTYPE, existing), False
    job = frappe.get_doc(
        {
            "doctype": PRINT_JOB_DOCTYPE,
            "job_key": job_key,
            "pos_order": order_name,
            "sales_invoice": invoice_name,
            "print_kind": group.print_kind,
            "status": "QUEUED",
        }
    ).insert(ignore_permissions=True)
    route_name = frappe.db.get_value(ROUTE_DOCTYPE, {"route_key": group.group_id}, "name")
    frappe.db.set_value(
        ROUTE_DOCTYPE,
        route_name,
        {"print_job": job.name, "status": "COMPLETED"},
        update_modified=False,
    )
    return job, True


def _route_evidence(frappe: Any, order_name: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        ROUTE_DOCTYPE,
        filters={"pos_order": order_name},
        fields=[
            "name",
            "route_key",
            "split_group_id",
            "company",
            "legal_entity",
            "fiscal_route",
            "status",
            "sales_invoice",
            "print_job",
        ],
        order_by="legal_entity asc, fiscal_route asc, name asc",
    )


def _sales_invoice_evidence(frappe: Any, invoice_names: list[str]) -> list[dict[str, Any]]:
    evidence = []
    for name in invoice_names:
        invoice = frappe.get_doc("Sales Invoice", name)
        evidence.append(
            {
                "name": invoice.name,
                "docstatus": invoice.docstatus,
                "grand_total": float(invoice.grand_total or 0),
                "paid_amount": float(invoice.paid_amount or 0),
                "group_id": invoice.get(GROUP_FIELD),
                "legal_entity": invoice.get(LEGAL_ENTITY_FIELD),
                "fiscal_route": invoice.get(FISCAL_ROUTE_FIELD),
                "idempotency_key": invoice.get(IDEMPOTENCY_FIELD),
                "payments": [
                    {"mode_of_payment": row.mode_of_payment, "amount": float(row.amount or 0)}
                    for row in invoice.payments
                ],
                "items": [
                    {
                        "row_id": row.name,
                        "item_code": row.item_code,
                        "qty": float(row.qty or 0),
                        "warehouse": row.warehouse,
                        "lot_name": row.get(DIMENSION_FIELD),
                        "relationship_model": row.get(RELATIONSHIP_FIELD),
                        "legal_entity": row.get(LEGAL_ENTITY_FIELD),
                        "fiscal_route": row.get(FISCAL_ROUTE_FIELD),
                    }
                    for row in invoice.items
                ],
            }
        )
    return evidence


def _make_return_invoice(
    frappe: Any,
    *,
    return_order: Any,
    original_invoice: Any,
    restoration: Any,
) -> Any:
    company_doc = frappe.get_cached_doc("Company", original_invoice.company)
    invoice = frappe.new_doc("Sales Invoice")
    invoice.company = original_invoice.company
    invoice.customer = return_order.customer
    invoice.posting_date = frappe.utils.nowdate()
    invoice.due_date = frappe.utils.nowdate()
    invoice.is_pos = 1
    invoice.update_stock = 1
    invoice.is_return = 1
    invoice.return_against = original_invoice.name
    invoice.currency = company_doc.default_currency
    invoice.conversion_rate = 1
    invoice.debit_to = company_doc.default_receivable_account
    invoice.set("ua_pos_order", return_order.name)
    invoice.set("ua_pos_desk", return_order.cash_desk)
    invoice.set("ua_pos_shift", return_order.operational_shift)
    invoice.set(GROUP_FIELD, f"{original_invoice.get(GROUP_FIELD)}:return")
    invoice.set(LEGAL_ENTITY_FIELD, original_invoice.get(LEGAL_ENTITY_FIELD))
    invoice.set(FISCAL_ROUTE_FIELD, original_invoice.get(FISCAL_ROUTE_FIELD))
    invoice.set(IDEMPOTENCY_FIELD, f"{original_invoice.get(IDEMPOTENCY_FIELD)}:return")
    original_row = original_invoice.items[0]
    row = invoice.append(
        "items",
        {
            "item_code": original_row.item_code,
            "item_name": original_row.item_name,
            "description": "Gate 0E return to original ownership",
            "warehouse": restoration.warehouse,
            "qty": -float(restoration.qty),
            "uom": original_row.uom,
            "stock_uom": original_row.stock_uom,
            "conversion_factor": original_row.conversion_factor,
            "rate": original_row.rate,
            "price_list_rate": original_row.price_list_rate,
            "income_account": original_row.income_account,
            "expense_account": original_row.expense_account,
            "cost_center": original_row.cost_center,
            "allow_zero_valuation_rate": 1,
            "serial_no": restoration.serial_no,
            "batch_no": restoration.batch_no,
        },
    )
    row.set(DIMENSION_FIELD, restoration.lot_name)
    row.set(RELATIONSHIP_FIELD, restoration.relationship_model)
    row.set(LEGAL_ENTITY_FIELD, original_row.get(LEGAL_ENTITY_FIELD))
    row.set(FISCAL_ROUTE_FIELD, original_row.get(FISCAL_ROUTE_FIELD))
    invoice.append("payments", {"mode_of_payment": "Cash", "amount": -float(original_invoice.grand_total)})
    invoice.set_missing_values()
    invoice.insert(ignore_permissions=True)
    invoice.submit()
    return invoice


def _cancel_documents(documents: list[Any]) -> tuple[list[str], list[str]]:
    cancelled = []
    errors = []
    seen = set()
    for document in reversed(documents):
        if not document or (document.doctype, document.name) in seen:
            continue
        seen.add((document.doctype, document.name))
        try:
            document.reload()
            if document.docstatus == 1:
                document.cancel()
                cancelled.append(document.name)
        except Exception as exc:  # pragma: no cover - integration evidence reports the exact failure
            errors.append(f"{document.doctype} {document.name}: {type(exc).__name__}: {exc}")
    return cancelled, errors


def _submitted_route_invoices(frappe: Any, order_names: list[str]) -> list[Any]:
    invoice_names = frappe.get_all(
        ROUTE_DOCTYPE,
        filters={"pos_order": ["in", order_names], "sales_invoice": ["is", "set"]},
        pluck="sales_invoice",
    )
    return [frappe.get_doc("Sales Invoice", name) for name in invoice_names]


def run_pos_saga_flow(
    confirm_site: str,
    confirm_write: str,
    company: str = "POS Test Ukraine",
) -> dict[str, Any]:
    """Verify the current POS Order boundary with split SI saga and exact-lot return."""
    import frappe

    _assert_test_scope(
        frappe,
        confirm_site=confirm_site,
        confirm_write=confirm_write,
        company=company,
    )
    frappe.set_user("Administrator")
    if not frappe.db.exists("DocType", "POS Order"):
        raise RuntimeError("Gate 0E requires the installed erpnext_ua POS Order")

    _ensure_reference_doctype(frappe)
    _ensure_dimension(frappe)
    _ensure_saga_doctypes(frappe)
    _ensure_custom_fields(frappe)
    _ensure_item(frappe)
    customer = _ensure_customer(frappe)
    context = _pos_context(frappe, company)
    context["customer"] = customer
    location, warehouses = _ensure_location_warehouses(frappe, company)

    run_id = frappe.generate_hash(length=10).upper()
    main_lots = {
        "OWN": f"TP-GATE-0E-{run_id}-OWN",
        "COMMISSION": f"TP-GATE-0E-{run_id}-COMMISSION",
        "CONSIGNMENT": f"TP-GATE-0E-{run_id}-CONSIGNMENT",
    }
    compensation_lots = {
        "OWN": f"TP-GATE-0E-{run_id}-COMP-OWN",
        "COMMISSION": f"TP-GATE-0E-{run_id}-COMP-COMMISSION",
    }
    all_lots = {**main_lots, **{f"COMP_{key}": value for key, value in compensation_lots.items()}}
    for lot_name in all_lots.values():
        _ensure_lot_value(frappe, lot_name)
    frappe.db.commit()

    receipt_documents: list[Any] = []
    return_invoices: list[Any] = []
    pos_orders: list[Any] = []
    result: dict[str, Any] = {
        "site": frappe.local.site,
        "company": company,
        "pos_order_doctype": {
            "exists": True,
            "module": frappe.db.get_value("DocType", "POS Order", "module"),
            "single_sales_invoice_field": frappe.get_meta("POS Order").has_field("sales_invoice"),
        },
        "context": context,
        "location": location,
        "warehouses": warehouses,
        "lots": all_lots,
    }

    try:
        receipt_specs = [
            ("OWN", main_lots["OWN"], Decimal("50"), "08:00:00"),
            ("COMMISSION", main_lots["COMMISSION"], Decimal("0"), "08:10:00"),
            ("CONSIGNMENT", main_lots["CONSIGNMENT"], Decimal("0"), "08:20:00"),
            ("OWN", compensation_lots["OWN"], Decimal("50"), "08:30:00"),
            ("COMMISSION", compensation_lots["COMMISSION"], Decimal("0"), "08:40:00"),
        ]
        for model, lot_name, rate, posting_time in receipt_specs:
            receipt = _make_stock_entry(
                item_code=ITEM_CODE,
                company=company,
                warehouse=warehouses[model],
                qty=1,
                inward=True,
                dimension_value=lot_name,
                rate=float(rate),
                posting_date=frappe.utils.nowdate(),
                posting_time=posting_time,
            )
            receipt_documents.append(receipt)
        frappe.db.commit()

        main_line_specs = [
            {
                "item_code": ITEM_CODE,
                "qty": Decimal("1"),
                "rate": Decimal("100"),
                "warehouse": warehouses["OWN"],
                "lot_name": main_lots["OWN"],
                "relationship_model": "OWN",
                "legal_entity": LEGAL_ENTITIES["A"],
                "fiscal_route": "FISCAL",
            },
            {
                "item_code": ITEM_CODE,
                "qty": Decimal("1"),
                "rate": Decimal("100"),
                "warehouse": warehouses["COMMISSION"],
                "lot_name": main_lots["COMMISSION"],
                "relationship_model": "COMMISSION",
                "legal_entity": LEGAL_ENTITIES["A"],
                "fiscal_route": "NON_FISCAL",
            },
            {
                "item_code": ITEM_CODE,
                "qty": Decimal("1"),
                "rate": Decimal("100"),
                "warehouse": warehouses["CONSIGNMENT"],
                "lot_name": main_lots["CONSIGNMENT"],
                "relationship_model": "CONSIGNMENT",
                "legal_entity": LEGAL_ENTITIES["B"],
                "fiscal_route": "FISCAL",
            },
        ]
        main_order = _make_pos_order(
            frappe,
            context=context,
            run_id=run_id,
            lines=main_line_specs,
            total=Decimal("300"),
            suffix="MAIN",
        )
        pos_orders.append(main_order)
        main_lines = _cart_lines(main_order, company)
        _create_reservations(frappe, main_order, main_lines)
        main_groups = split_checkout(
            main_order.name,
            main_lines,
            fiscal_checkout=True,
            commission_is_fiscal=False,
        )
        main_allocations = allocate_payment_plan(
            main_groups,
            [PaymentTender("MAIN-CASH", "Cash", Decimal("300"))],
        )

        try:
            _execute_checkout(
                frappe,
                order=main_order,
                groups=main_groups,
                allocations=main_allocations,
                timeout_after=1,
            )
        except SimulatedTimeout as exc:
            result["simulated_timeout"] = str(exc)
        else:
            raise AssertionError("Gate 0E timeout probe did not interrupt the first pass")

        retry_result = _execute_checkout(
            frappe,
            order=main_order,
            groups=main_groups,
            allocations=main_allocations,
        )
        second_retry_result = _execute_checkout(
            frappe,
            order=main_order,
            groups=main_groups,
            allocations=main_allocations,
        )
        main_invoice_names = [
            frappe.db.get_value(ROUTE_DOCTYPE, {"route_key": group.group_id}, "sales_invoice")
            for group in main_groups
        ]
        _set_reservations(frappe, main_order.name, "CONSUMED")

        first_print_pass = {"created": [], "reused": []}
        second_print_pass = {"created": [], "reused": []}
        for group, invoice_name in zip(main_groups, main_invoice_names, strict=True):
            job, created = _queue_print_job(frappe, main_order.name, group, invoice_name)
            first_print_pass["created" if created else "reused"].append(job.name)
        for group, invoice_name in zip(main_groups, main_invoice_names, strict=True):
            job, created = _queue_print_job(frappe, main_order.name, group, invoice_name)
            second_print_pass["created" if created else "reused"].append(job.name)

        main_order.reload()
        main_order.status = "Completed"
        main_order.save(ignore_permissions=True)
        frappe.db.commit()

        commission_group = next(
            group
            for group in main_groups
            if any(line.relationship_model == "COMMISSION" for line in group.lines)
        )
        commission_invoice = frappe.get_doc(
            "Sales Invoice",
            frappe.db.get_value(ROUTE_DOCTYPE, {"route_key": commission_group.group_id}, "sales_invoice"),
        )
        commission_line = commission_group.lines[0]
        balance_after_sale = _dimension_balance(
            frappe,
            ITEM_CODE,
            commission_line.warehouse,
            commission_line.lot_name,
        )
        restoration = plan_return(
            [
                ReturnSource(
                    allocation_id=f"{commission_group.group_id}:allocation:1",
                    original_row_id=commission_line.row_id,
                    lot_name=commission_line.lot_name,
                    warehouse=commission_line.warehouse,
                    relationship_model=commission_line.relationship_model,
                    sold_qty=commission_line.qty,
                )
            ],
            {f"{commission_group.group_id}:allocation:1": Decimal("1")},
        )[0]
        return_order = _make_pos_order(
            frappe,
            context=context,
            run_id=run_id,
            lines=[
                {
                    "item_code": ITEM_CODE,
                    "qty": restoration.qty,
                    "rate": Decimal("100"),
                    "warehouse": restoration.warehouse,
                    "lot_name": restoration.lot_name,
                    "relationship_model": restoration.relationship_model,
                    "legal_entity": commission_group.key.legal_entity,
                    "fiscal_route": commission_group.key.fiscal_route,
                    "return_against_item": restoration.original_row_id,
                }
            ],
            total=Decimal("100"),
            suffix="RETURN",
            return_against=main_order.name,
        )
        pos_orders.append(return_order)
        return_invoice = _make_return_invoice(
            frappe,
            return_order=return_order,
            original_invoice=commission_invoice,
            restoration=restoration,
        )
        return_invoices.append(return_invoice)
        return_order.sales_invoice = return_invoice.name
        return_order.status = "Completed"
        return_order.save(ignore_permissions=True)
        balance_after_return = _dimension_balance(
            frappe,
            ITEM_CODE,
            commission_line.warehouse,
            commission_line.lot_name,
        )

        compensation_line_specs = [
            {
                "item_code": ITEM_CODE,
                "qty": Decimal("1"),
                "rate": Decimal("100"),
                "warehouse": warehouses["OWN"],
                "lot_name": compensation_lots["OWN"],
                "relationship_model": "OWN",
                "legal_entity": LEGAL_ENTITIES["A"],
                "fiscal_route": "FISCAL",
            },
            {
                "item_code": ITEM_CODE,
                "qty": Decimal("1"),
                "rate": Decimal("100"),
                "warehouse": warehouses["COMMISSION"],
                "lot_name": compensation_lots["COMMISSION"],
                "relationship_model": "COMMISSION",
                "legal_entity": LEGAL_ENTITIES["A"],
                "fiscal_route": "NON_FISCAL",
            },
        ]
        compensation_order = _make_pos_order(
            frappe,
            context=context,
            run_id=run_id,
            lines=compensation_line_specs,
            total=Decimal("200"),
            suffix="COMPENSATION",
        )
        pos_orders.append(compensation_order)
        compensation_lines = _cart_lines(compensation_order, company)
        _create_reservations(frappe, compensation_order, compensation_lines)
        compensation_groups = split_checkout(
            compensation_order.name,
            compensation_lines,
            fiscal_checkout=True,
        )
        compensation_allocations = allocate_payment_plan(
            compensation_groups,
            [PaymentTender("COMP-CASH", "Cash", Decimal("200"))],
        )
        first_group = compensation_groups[0]
        first_route = _ensure_route(frappe, compensation_order.name, first_group)
        compensated_invoice, created = _make_split_invoice(
            frappe,
            order=compensation_order,
            group=first_group,
            allocations=compensation_allocations,
            route=first_route,
        )
        if not created:
            raise AssertionError("Compensation probe unexpectedly reused its first invoice")
        frappe.db.commit()
        compensation_order.status = "Compensating"
        compensation_order.save(ignore_permissions=True)
        progress = [
            RouteProgress(
                group_id=first_group.group_id,
                status="SUBMITTED",
                sales_invoice=compensated_invoice.name,
            )
        ]
        compensation_actions = plan_compensation(progress)
        for action in compensation_actions:
            if action.action == "CANCEL_SALES_INVOICE":
                invoice = frappe.get_doc("Sales Invoice", action.document_name)
                invoice.cancel()
                frappe.db.set_value(
                    ROUTE_DOCTYPE,
                    first_route.name,
                    {"status": "COMPENSATED", "failure_note": "Simulated second-route failure"},
                    update_modified=False,
                )
            elif action.action == "RELEASE_RESERVATIONS":
                _set_reservations(frappe, compensation_order.name, "RELEASED")
        compensation_order.status = "Cancelled"
        compensation_order.save(ignore_permissions=True)
        compensation_balances = {
            model: _dimension_balance(frappe, ITEM_CODE, warehouses[model], lot_name)
            for model, lot_name in compensation_lots.items()
        }

        route_evidence = _route_evidence(frappe, main_order.name)
        sales_invoice_evidence = _sales_invoice_evidence(frappe, main_invoice_names)
        print_jobs = frappe.get_all(
            PRINT_JOB_DOCTYPE,
            filters={"pos_order": main_order.name},
            fields=["name", "job_key", "sales_invoice", "print_kind", "status"],
            order_by="print_kind asc, name asc",
        )
        result.update(
            {
                "main_order": {
                    "name": main_order.name,
                    "lookup_token": main_order.lookup_token,
                    "logical_payment_plan": [
                        {
                            "mode_of_payment": row.mode_of_payment,
                            "amount": float(row.amount or 0),
                            "status": row.status,
                        }
                        for row in main_order.payments_plan
                    ],
                    "groups": [
                        {
                            "group_id": group.group_id,
                            "company": group.key.company,
                            "legal_entity": group.key.legal_entity,
                            "fiscal_route": group.key.fiscal_route,
                            "print_kind": group.print_kind,
                            "total": float(group.total),
                            "models": [line.relationship_model for line in group.lines],
                        }
                        for group in main_groups
                    ],
                    "payment_allocations": [
                        {
                            "tender_id": row.tender_id,
                            "mode_of_payment": row.mode_of_payment,
                            "group_id": row.group_id,
                            "amount": float(row.amount),
                        }
                        for row in main_allocations
                    ],
                    "routes": route_evidence,
                    "sales_invoices": sales_invoice_evidence,
                },
                "retry": {
                    "first_retry": retry_result,
                    "second_retry": second_retry_result,
                    "route_count": len(route_evidence),
                    "invoice_count": len(main_invoice_names),
                },
                "print_jobs": {
                    "first_pass": first_print_pass,
                    "second_pass": second_print_pass,
                    "jobs": print_jobs,
                },
                "return": {
                    "lookup_token": main_order.lookup_token,
                    "return_order": return_order.name,
                    "original_sales_invoice": commission_invoice.name,
                    "return_sales_invoice": return_invoice.name,
                    "lot_name": restoration.lot_name,
                    "warehouse": restoration.warehouse,
                    "relationship_model": restoration.relationship_model,
                    "balance_after_sale": balance_after_sale,
                    "balance_after_return": balance_after_return,
                    "stock_ledger": _ledger_evidence(frappe, [return_invoice.name]),
                },
                "compensation": {
                    "order": compensation_order.name,
                    "submitted_before_failure": compensated_invoice.name,
                    "actions": [
                        {
                            "action": action.action,
                            "group_id": action.group_id,
                            "document_name": action.document_name,
                        }
                        for action in compensation_actions
                    ],
                    "invoice_docstatus_after_compensation": frappe.db.get_value(
                        "Sales Invoice", compensated_invoice.name, "docstatus"
                    ),
                    "reservation_statuses": frappe.get_all(
                        RESERVATION_DOCTYPE,
                        filters={"pos_order": compensation_order.name},
                        fields=["stock_lot", "qty", "status"],
                        order_by="stock_lot asc",
                    ),
                    "balances": compensation_balances,
                },
            }
        )
    finally:
        route_invoices = _submitted_route_invoices(frappe, [order.name for order in pos_orders])
        cancelled_returns, return_cancel_errors = _cancel_documents(return_invoices)
        cancelled_routes, route_cancel_errors = _cancel_documents(route_invoices)
        balances_after_invoice_cancel = {
            key: _dimension_balance(
                frappe,
                ITEM_CODE,
                warehouses["OWN"] if "OWN" in key else (
                    warehouses["CONSIGNMENT"] if "CONSIGNMENT" in key else warehouses["COMMISSION"]
                ),
                lot_name,
            )
            for key, lot_name in all_lots.items()
        }
        for order in pos_orders:
            _set_reservations(frappe, order.name, "RELEASED")
            order.reload()
            if order.status != "Cancelled":
                order.status = "Cancelled"
                order.save(ignore_permissions=True)
        cancelled_receipts, receipt_cancel_errors = _cancel_documents(receipt_documents)
        balances_after_cleanup = {
            key: _dimension_balance(
                frappe,
                ITEM_CODE,
                warehouses["OWN"] if "OWN" in key else (
                    warehouses["CONSIGNMENT"] if "CONSIGNMENT" in key else warehouses["COMMISSION"]
                ),
                lot_name,
            )
            for key, lot_name in all_lots.items()
        }
        result["cleanup"] = {
            "cancelled_return_invoices": cancelled_returns,
            "cancelled_route_invoices": cancelled_routes,
            "cancelled_receipts": cancelled_receipts,
            "balances_after_invoice_cancel": balances_after_invoice_cancel,
            "balances_after_cleanup": balances_after_cleanup,
            "errors": [*return_cancel_errors, *route_cancel_errors, *receipt_cancel_errors],
        }
        frappe.db.commit()

    if result["cleanup"]["errors"]:
        raise AssertionError(f"Gate 0E cleanup failed: {result['cleanup']['errors']}")
    if len(result["main_order"]["groups"]) != 3:
        raise AssertionError(f"Mixed checkout did not split into three legal/fiscal routes: {result}")
    if result["retry"]["first_retry"]["created"] == [] or len(result["retry"]["first_retry"]["reused"]) != 1:
        raise AssertionError(f"Retry did not reuse the route committed before timeout: {result}")
    if result["retry"]["second_retry"]["created"] or len(result["retry"]["second_retry"]["reused"]) != 3:
        raise AssertionError(f"Second retry created duplicate invoices: {result}")
    if len(result["print_jobs"]["jobs"]) != 3 or len(result["print_jobs"]["second_pass"]["reused"]) != 3:
        raise AssertionError(f"Print job idempotency failed: {result}")
    if {job["print_kind"] for job in result["print_jobs"]["jobs"]} != {
        "FISCAL_RECEIPT",
        "NON_FISCAL_GOODS_RECEIPT",
    }:
        raise AssertionError(f"Fiscal and non-fiscal print jobs were not separated: {result}")
    if result["return"]["balance_after_sale"] != 0 or result["return"]["balance_after_return"] != 1:
        raise AssertionError(f"Return did not restore the original lot balance: {result}")
    if result["compensation"]["invoice_docstatus_after_compensation"] != 2:
        raise AssertionError(f"Partially posted checkout was not compensated: {result}")
    if any(row["status"] != "RELEASED" for row in result["compensation"]["reservation_statuses"]):
        raise AssertionError(f"Compensation did not release reservations: {result}")
    if any(result["cleanup"]["balances_after_cleanup"].values()):
        raise AssertionError(f"Gate 0E left active stock after cleanup: {result}")
    return result
