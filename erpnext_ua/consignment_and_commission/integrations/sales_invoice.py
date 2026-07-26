"""Controlled Sales Invoice lifecycle for allocation-backed CC stock."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
from typing import Any

from ..services.reservation import ReservationError
from ..services.sale import (
    ManagedSaleError,
    ManagedSaleRequest,
    managed_sale_fingerprint,
    validate_managed_sale_request,
)
from ..services.sale_financials import SaleFinancialError
from ..setup.ownership_dimension import (
    ALLOCATION_FIELD,
    ALLOCATION_SLICE_FIELD,
    MANAGED_RETURN_FIELD,
    MANAGED_SALE_FIELD,
    OWNERSHIP_FIELD,
    POS_CHECKOUT_FIELD,
    POS_ORDER_FIELD,
    POS_ROUTE_FIELD,
    RELATIONSHIP_MODEL_FIELD,
    RETURN_SALE_ALLOCATION_FIELD,
    SALE_FINGERPRINT_FIELD,
    SALE_IDEMPOTENCY_FIELD,
    SOURCE_METHOD_FIELD,
)
from .pricing import PriceResolutionError
from .reservations import consume_allocation, release_allocation
from .sale_allocations import (
    cancel_sale_recognition,
    get_account_mapping,
    mark_sale_allocations_cancelled,
    post_sale_allocations_and_recognition,
    validate_sale_financials,
)
from .tracking import get_tracking_selections


def _assert_schema(frappe: Any) -> None:
    required = (
        ("Sales Invoice", MANAGED_SALE_FIELD),
        ("Sales Invoice", MANAGED_RETURN_FIELD),
        ("Sales Invoice", SALE_IDEMPOTENCY_FIELD),
        ("Sales Invoice", SALE_FINGERPRINT_FIELD),
        ("Sales Invoice", POS_CHECKOUT_FIELD),
        ("Sales Invoice", POS_ROUTE_FIELD),
        ("Sales Invoice", POS_ORDER_FIELD),
        ("Sales Invoice Item", OWNERSHIP_FIELD),
        ("Sales Invoice Item", ALLOCATION_FIELD),
        ("Sales Invoice Item", ALLOCATION_SLICE_FIELD),
        ("Sales Invoice Item", SOURCE_METHOD_FIELD),
        ("Sales Invoice Item", RELATIONSHIP_MODEL_FIELD),
        ("Sales Invoice Item", RETURN_SALE_ALLOCATION_FIELD),
    )
    missing = [
        f"{doctype}.{fieldname}"
        for doctype, fieldname in required
        if not frappe.db.has_column(doctype, fieldname)
    ]
    if missing:
        raise ManagedSaleError(
            f"CC managed sale schema is incomplete; run bench migrate: {', '.join(missing)}"
        )


def _existing_sale(frappe: Any, request: ManagedSaleRequest, fingerprint: str) -> Any | None:
    name = frappe.db.get_value(
        "Sales Invoice",
        {SALE_IDEMPOTENCY_FIELD: request.idempotency_key},
        "name",
    )
    if not name:
        return None
    invoice = frappe.get_doc("Sales Invoice", name)
    if invoice.get(SALE_FINGERPRINT_FIELD) != fingerprint:
        raise ManagedSaleError(
            f"Sale idempotency key {request.idempotency_key!r} already belongs to another request"
        )
    return invoice


def _load_reserved_allocations(frappe: Any, request: ManagedSaleRequest) -> list[Any]:
    from frappe.utils import get_datetime, now_datetime

    allocations = []
    company = None
    location = None
    for line in request.lines:
        if not frappe.db.exists("CC Allocation", line.allocation):
            raise ManagedSaleError(f"CC Allocation {line.allocation} does not exist")
        allocation = frappe.get_doc("CC Allocation", line.allocation)
        if allocation.status != "RESERVED":
            raise ManagedSaleError(
                f"CC Allocation {allocation.name} must be RESERVED, not {allocation.status}"
            )
        if get_datetime(allocation.expires_at) <= now_datetime():
            raise ManagedSaleError(f"CC Allocation {allocation.name} has expired")
        if company and allocation.company != company:
            raise ManagedSaleError("One managed Sales Invoice cannot mix Companies")
        if location and allocation.location != location:
            raise ManagedSaleError("One managed Sales Invoice cannot mix CC Locations")
        company = allocation.company
        location = allocation.location

        existing_parent = frappe.db.sql(
            f"""
            select item.parent
            from `tabSales Invoice Item` item
            inner join `tabSales Invoice` invoice on invoice.name = item.parent
            where item.`{ALLOCATION_FIELD}` = %s
              and invoice.docstatus != 2
            limit 1
            """,
            (allocation.name,),
        )
        if existing_parent:
            raise ManagedSaleError(
                f"CC Allocation {allocation.name} is already linked to Sales Invoice "
                f"{existing_parent[0][0]}"
            )
        allocations.append(allocation)
    return allocations


def _resolve_selling_price_list(frappe: Any, customer: str, currency: str) -> str:
    preferred = (
        frappe.db.get_value("Customer", customer, "default_price_list"),
        frappe.db.get_single_value("Selling Settings", "selling_price_list"),
    )
    for name in preferred:
        if not name:
            continue
        values = frappe.db.get_value(
            "Price List",
            name,
            ["currency", "enabled", "selling"],
            as_dict=True,
        )
        if values and values.enabled and values.selling and values.currency == currency:
            return name
    rows = frappe.get_all(
        "Price List",
        filters={"currency": currency, "enabled": 1, "selling": 1},
        pluck="name",
        order_by="name asc",
        limit=1,
    )
    if not rows:
        raise ManagedSaleError(
            f"Managed sale requires an enabled Selling Price List in {currency}"
        )
    return rows[0]


def create_sales_invoice_from_allocations(request: ManagedSaleRequest) -> Any:
    """Create one idempotent draft SI from already committed FIFO reservations."""
    import frappe
    from erpnext.accounts.party import get_party_account
    from frappe.utils import nowdate

    if frappe.db.transaction_writes:
        raise ManagedSaleError(
            "Managed Sales Invoice creation must start before unrelated transaction writes"
        )
    validate_managed_sale_request(request)
    _assert_schema(frappe)
    settings = frappe.get_single("CC Settings")
    if not settings.enabled:
        raise ManagedSaleError("CC Settings must be enabled before a managed sale can be created")

    fingerprint = managed_sale_fingerprint(request)
    existing = _existing_sale(frappe, request, fingerprint)
    if existing:
        return existing
    if not frappe.db.exists("Customer", request.customer):
        raise ManagedSaleError(f"Customer {request.customer} does not exist")

    allocations = _load_reserved_allocations(frappe, request)
    company_name = allocations[0].company
    company = frappe.get_cached_value(
        "Company",
        company_name,
        [
            "default_currency",
            "default_income_account",
            "default_expense_account",
            "cost_center",
        ],
        as_dict=True,
    )
    if not company or not all(
        (
            company.default_currency,
            company.default_income_account,
            company.default_expense_account,
            company.cost_center,
        )
    ):
        raise ManagedSaleError("Sale Company requires default currency, accounts and Cost Center")
    invoice_currency = request.currency or company.default_currency
    conversion_rate = request.conversion_rate or Decimal("1")
    if invoice_currency == company.default_currency and conversion_rate != 1:
        raise ManagedSaleError("Company-currency managed sale requires conversion rate 1")
    if not frappe.db.exists("Currency", invoice_currency):
        raise ManagedSaleError(f"Sale Currency {invoice_currency} does not exist")
    selling_price_list = _resolve_selling_price_list(
        frappe,
        request.customer,
        invoice_currency,
    )
    receivable = get_party_account("Customer", request.customer, company_name)
    receivable_currency = (
        frappe.db.get_value(
            "Account",
            receivable,
            "account_currency",
        )
        if receivable
        else None
    )
    if not receivable or receivable_currency != invoice_currency:
        raise ManagedSaleError(
            f"Sale Customer requires a {invoice_currency} Receivable account for the Company"
        )
    has_third_party = any(
        allocation_slice.relationship_model != "OWN"
        for allocation in allocations
        for allocation_slice in allocation.slices
    )
    account_mapping = get_account_mapping(frappe, company_name) if has_third_party else None

    invoice = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "company": company_name,
            "customer": request.customer,
            "posting_date": request.posting_date or nowdate(),
            "due_date": request.posting_date or nowdate(),
            "update_stock": 1,
            "currency": invoice_currency,
            "conversion_rate": conversion_rate,
            "selling_price_list": selling_price_list,
            "price_list_currency": invoice_currency,
            "plc_conversion_rate": 1,
            "debit_to": receivable,
            MANAGED_SALE_FIELD: 1,
            SALE_IDEMPOTENCY_FIELD: request.idempotency_key,
            SALE_FINGERPRINT_FIELD: fingerprint,
            POS_CHECKOUT_FIELD: request.pos_checkout,
            POS_ROUTE_FIELD: request.pos_route,
            POS_ORDER_FIELD: request.pos_order,
        }
    )
    for line, allocation in zip(request.lines, allocations, strict=True):
        item = frappe.get_cached_value(
            "Item",
            allocation.item_code,
            ["item_name", "description", "stock_uom"],
            as_dict=True,
        )
        if not item or not item.stock_uom:
            raise ManagedSaleError(f"Item {allocation.item_code} has no Stock UOM")
        for allocation_slice in allocation.slices:
            income_account = (
                account_mapping.gross_proceeds_clearing_account
                if allocation_slice.relationship_model != "OWN"
                else company.default_income_account
            )
            row = invoice.append(
                "items",
                {
                    "item_code": allocation.item_code,
                    "item_name": item.item_name,
                    "description": item.description or item.item_name,
                    "warehouse": allocation_slice.warehouse,
                    "qty": allocation_slice.qty,
                    "uom": item.stock_uom,
                    "stock_uom": item.stock_uom,
                    "conversion_factor": 1,
                    "rate": float(line.rate),
                    "price_list_rate": float(line.rate),
                    "income_account": income_account,
                    "expense_account": company.default_expense_account,
                    "cost_center": company.cost_center,
                    "allow_zero_valuation_rate": int(
                        allocation_slice.relationship_model != "OWN"
                    ),
                    "use_serial_batch_fields": int(
                        bool(allocation_slice.serial_no or allocation_slice.batch_no)
                    ),
                    "serial_no": allocation_slice.serial_no,
                    "batch_no": allocation_slice.batch_no,
                    ALLOCATION_FIELD: allocation.name,
                    ALLOCATION_SLICE_FIELD: allocation_slice.name,
                    SOURCE_METHOD_FIELD: allocation_slice.source_method,
                    RELATIONSHIP_MODEL_FIELD: allocation_slice.relationship_model,
                },
            )
            row.set(OWNERSHIP_FIELD, allocation_slice.stock_lot)

    invoice_name = "CC-SI-" + sha256(
        f"{request.idempotency_key}:{fingerprint}".encode()
    ).hexdigest()[:20].upper()
    savepoint = "cc_managed_sale_create"
    frappe.db.savepoint(savepoint)
    try:
        invoice.insert(ignore_permissions=True, set_name=invoice_name)
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=savepoint)
        existing = _existing_sale(frappe, request, fingerprint)
        if existing:
            return existing
        raise ManagedSaleError("Concurrent managed sale creation did not settle") from None
    except frappe.QueryDeadlockError:
        frappe.db.rollback()
        existing = _existing_sale(frappe, request, fingerprint)
        if existing:
            return existing
        raise ManagedSaleError("Concurrent managed sale database conflict did not settle") from None
    return invoice


def _technical_warehouses(frappe: Any) -> set[str]:
    values: set[str] = set()
    for location in frappe.get_all(
        "CC Location",
        fields=["own_warehouse", "commission_warehouse", "consignment_warehouse"],
    ):
        values.update(
            value
            for value in (
                location.own_warehouse,
                location.commission_warehouse,
                location.consignment_warehouse,
            )
            if value
        )
    return values


def _tracking_names(frappe: Any, row: Any, doctype: str) -> set[str]:
    return {
        selection.name
        for selection in get_tracking_selections(frappe, row)
        if selection.doctype == doctype
    }


def validate_managed_sales_invoice(doc: Any, method: str | None = None) -> None:
    """Fail closed on every submitted SI that touches a CC technical Warehouse."""
    del method
    import frappe
    from frappe.utils import get_datetime, now_datetime

    warehouses = _technical_warehouses(frappe)
    cc_rows = [row for row in doc.items if row.warehouse in warehouses]
    if not cc_rows:
        return
    settings = frappe.get_single("CC Settings")
    if not settings.enabled:
        if doc.get(MANAGED_SALE_FIELD) or doc.get(MANAGED_RETURN_FIELD):
            frappe.throw("CC Settings must be enabled before a managed sale can be submitted")
        return
    if doc.get("is_return"):
        from .sale_returns import validate_return_invoice

        validate_return_invoice(doc)
        return
    if not doc.get(MANAGED_SALE_FIELD):
        frappe.throw("CC technical Warehouse stock must be sold through a managed FIFO allocation")
    if not doc.get("update_stock"):
        frappe.throw("Managed CC Sales Invoice must update stock")
    if len(cc_rows) != len(doc.items):
        frappe.throw("Managed CC Sales Invoice cannot mix CC and ordinary Warehouse rows")

    rows_by_allocation: dict[str, list[Any]] = defaultdict(list)
    for row in cc_rows:
        allocation_name = row.get(ALLOCATION_FIELD)
        if not allocation_name or not row.get(ALLOCATION_SLICE_FIELD):
            frappe.throw(f"Row {row.idx}: managed CC stock requires an Allocation and Slice")
        rows_by_allocation[allocation_name].append(row)

    for allocation_name, rows in rows_by_allocation.items():
        allocation = frappe.get_doc("CC Allocation", allocation_name)
        consumed_by_this_invoice = (
            allocation.status == "CONSUMED"
            and allocation.consumer_doctype == "Sales Invoice"
            and allocation.consumer_document == doc.name
        )
        if allocation.status != "RESERVED" and not consumed_by_this_invoice:
            frappe.throw(
                f"CC Allocation {allocation.name} must be RESERVED, not {allocation.status}"
            )
        if allocation.status == "RESERVED" and get_datetime(allocation.expires_at) <= now_datetime():
            frappe.throw(f"CC Allocation {allocation.name} has expired")
        if allocation.company != doc.company:
            frappe.throw(f"CC Allocation {allocation.name} belongs to another Company")

        slices = {row.name: row for row in allocation.slices}
        requested_slice_names = {row.get(ALLOCATION_SLICE_FIELD) for row in rows}
        if requested_slice_names != set(slices):
            frappe.throw(f"Sales Invoice must consume every slice of CC Allocation {allocation.name}")
        if len(requested_slice_names) != len(rows):
            frappe.throw(f"CC Allocation {allocation.name} contains a duplicate Slice")

        for invoice_row in rows:
            allocation_slice = slices[invoice_row.get(ALLOCATION_SLICE_FIELD)]
            expected = {
                "item_code": allocation.item_code,
                "warehouse": allocation_slice.warehouse,
                OWNERSHIP_FIELD: allocation_slice.stock_lot,
                SOURCE_METHOD_FIELD: allocation_slice.source_method,
                RELATIONSHIP_MODEL_FIELD: allocation_slice.relationship_model,
            }
            mismatches = [
                fieldname
                for fieldname, value in expected.items()
                if str(invoice_row.get(fieldname) or "") != str(value or "")
            ]
            actual_qty = Decimal(str(invoice_row.stock_qty or 0))
            if actual_qty != Decimal(str(allocation_slice.qty)):
                mismatches.append("stock_qty")
            serials = _tracking_names(frappe, invoice_row, "Serial No")
            batches = _tracking_names(frappe, invoice_row, "Batch")
            expected_serials = {allocation_slice.serial_no} if allocation_slice.serial_no else set()
            expected_batches = {allocation_slice.batch_no} if allocation_slice.batch_no else set()
            if serials != expected_serials:
                mismatches.append("serial_no")
            if batches != expected_batches:
                mismatches.append("batch_no")
            if mismatches:
                frappe.throw(
                    f"Row {invoice_row.idx}: CC Allocation Slice changed: "
                    f"{', '.join(dict.fromkeys(mismatches))}"
                )
    if doc.get(POS_ROUTE_FIELD):
        from .pos import validate_pos_route_invoice

        validate_pos_route_invoice(doc)
    try:
        validate_sale_financials(doc)
    except (SaleFinancialError, PriceResolutionError) as exc:
        frappe.throw(str(exc))


def consume_sales_invoice_allocations(doc: Any, method: str | None = None) -> None:
    """Consume every exact reservation only after ERPNext submits its SI."""
    del method
    if doc.get(MANAGED_RETURN_FIELD):
        from .sale_returns import post_return_allocations_and_reversal

        post_return_allocations_and_reversal(doc)
        return
    if not doc.get(MANAGED_SALE_FIELD):
        return
    allocation_names = tuple(
        dict.fromkeys(row.get(ALLOCATION_FIELD) for row in doc.items if row.get(ALLOCATION_FIELD))
    )
    for allocation_name in allocation_names:
        allocation = consume_allocation(
            allocation_name,
            consumer_doctype="Sales Invoice",
            consumer_document=doc.name,
        )
        if allocation.status != "CONSUMED":
            raise ReservationError(f"CC Allocation {allocation_name} was not consumed")
    post_sale_allocations_and_recognition(doc)


def before_cancel_managed_sales_invoice(doc: Any, method: str | None = None) -> None:
    del method
    if doc.get(MANAGED_SALE_FIELD) or doc.get(MANAGED_RETURN_FIELD):
        from .off_balance import cancel_reference_off_balance

        cancel_reference_off_balance(doc)
    if doc.get(MANAGED_RETURN_FIELD):
        from .sale_returns import cancel_return_reversal

        cancel_return_reversal(doc)
        return
    if doc.get(MANAGED_SALE_FIELD):
        cancel_sale_recognition(doc)


def on_cancel_managed_sales_invoice(doc: Any, method: str | None = None) -> None:
    del method
    if doc.get(MANAGED_RETURN_FIELD):
        from .sale_returns import cancel_return_allocations

        cancel_return_allocations(doc)
        return
    if doc.get(MANAGED_SALE_FIELD):
        mark_sale_allocations_cancelled(doc)


def release_draft_sales_invoice_allocations(doc: Any, method: str | None = None) -> None:
    """Release held stock when its unsubmitted managed draft is deleted."""
    del method
    import frappe

    if not doc.get(MANAGED_SALE_FIELD) or doc.docstatus != 0:
        return
    allocation_names = tuple(
        dict.fromkeys(row.get(ALLOCATION_FIELD) for row in doc.items if row.get(ALLOCATION_FIELD))
    )
    for allocation_name in allocation_names:
        status = frappe.db.get_value("CC Allocation", allocation_name, "status")
        if status == "RESERVED":
            release_allocation(
                allocation_name,
                reason=f"Managed draft Sales Invoice {doc.name} was deleted",
            )
