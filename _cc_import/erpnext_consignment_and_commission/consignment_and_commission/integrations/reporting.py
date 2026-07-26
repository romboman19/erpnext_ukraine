"""Permission-aware operational reporting over immutable CC evidence."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _permitted_companies(frappe: Any, requested: str | None = None) -> list[str]:
    permitted = set(frappe.get_list("Company", pluck="name", limit=0))
    if requested:
        if requested not in permitted:
            frappe.throw(f"Not permitted to read Company {requested}", frappe.PermissionError)
        return [requested]
    return sorted(permitted)


def _in_clause(values: list[str]) -> tuple[str, tuple[str, ...]]:
    if not values:
        return "", ()
    return ", ".join(["%s"] * len(values)), tuple(values)


def get_fifo_inventory(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return ledger balance, live reservations and deterministic FIFO position per lot."""
    import frappe
    from frappe.utils import date_diff, getdate, nowdate

    filters = dict(filters or {})
    companies = _permitted_companies(frappe, filters.get("company"))
    if not companies:
        return []
    lot_filters: dict[str, Any] = {"company": ("in", companies)}
    for fieldname in (
        "location",
        "item_code",
        "partner_profile",
        "supplier",
        "source_method",
        "relationship_model",
        "lot_status",
    ):
        if filters.get(fieldname):
            lot_filters[fieldname] = filters[fieldname]
    lots = frappe.get_all(
        "CC Stock Lot",
        filters=lot_filters,
        fields=[
            "name",
            "company",
            "location",
            "partner_profile",
            "supplier",
            "contract",
            "item_code",
            "warehouse",
            "source_method",
            "relationship_model",
            "lot_status",
            "tracking_type",
            "batch_no",
            "received_datetime",
            "received_qty",
            "reserved_qty",
            "blocked_reason",
        ],
        order_by="company, location, item_code, received_datetime, source_method, name",
        limit=0,
    )
    if not lots:
        return []
    names = [row.name for row in lots]
    placeholders, values = _in_clause(names)
    ledger = {
        row.stock_lot: _decimal(row.balance)
        for row in frappe.db.sql(
            f"""
            select cc_stock_lot as stock_lot, sum(actual_qty) as balance
            from `tabStock Ledger Entry`
            where cc_stock_lot in ({placeholders}) and is_cancelled = 0
            group by cc_stock_lot
            """,
            values,
            as_dict=True,
        )
    }
    reservations = {
        row.stock_lot: _decimal(row.reserved_qty)
        for row in frappe.db.sql(
            f"""
            select slice.stock_lot, sum(slice.qty) as reserved_qty
            from `tabCC Allocation Slice` slice
            inner join `tabCC Allocation` allocation on allocation.name = slice.parent
            where slice.stock_lot in ({placeholders}) and allocation.status = 'RESERVED'
            group by slice.stock_lot
            """,
            values,
            as_dict=True,
        )
    }
    positions: dict[tuple[str, str, str], int] = defaultdict(int)
    today = getdate(nowdate())
    result = []
    for lot in lots:
        balance = ledger.get(lot.name, Decimal("0"))
        active_reserved = reservations.get(lot.name, Decimal("0"))
        available = balance - active_reserved
        key = (lot.company, lot.location, lot.item_code)
        fifo_position = None
        if available > 0 and lot.lot_status == "OPEN":
            positions[key] += 1
            fifo_position = positions[key]
        if filters.get("available_only") and available <= 0:
            continue
        result.append(
            {
                **dict(lot),
                "ledger_balance": balance,
                "active_reserved_qty": active_reserved,
                "available_qty": available,
                "reservation_variance": _decimal(lot.reserved_qty) - active_reserved,
                "fifo_position": fifo_position,
                "age_days": max(date_diff(today, getdate(lot.received_datetime)), 0),
            }
        )
    return result


def get_sale_financials(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return immutable sale, return, income and settlement snapshots per FIFO slice."""
    import frappe

    filters = dict(filters or {})
    companies = _permitted_companies(frappe, filters.get("company"))
    if not companies:
        return []
    placeholders, company_values = _in_clause(companies)
    conditions = [f"sale.company in ({placeholders})"]
    values: list[Any] = list(company_values)
    for fieldname in (
        "supplier",
        "contract",
        "customer",
        "item_code",
        "source_method",
        "relationship_model",
        "status",
        "sales_invoice",
        "settlement_report",
    ):
        if filters.get(fieldname):
            conditions.append(f"sale.`{fieldname}` = %s")
            values.append(filters[fieldname])
    if filters.get("from_date"):
        conditions.append("sale.posting_date >= %s")
        values.append(filters["from_date"])
    if filters.get("to_date"):
        conditions.append("sale.posting_date <= %s")
        values.append(filters["to_date"])
    return frappe.db.sql(
        f"""
        select
            sale.name, sale.posting_date, sale.company, sale.sales_invoice,
            sale.customer, sale.item_code, sale.stock_lot, sale.source_method,
            sale.relationship_model, sale.supplier, sale.contract, sale.currency,
            sale.status, sale.sold_qty, sale.returned_qty,
            sale.net_amount, coalesce(ret.net_amount, 0) as returned_net_amount,
            sale.net_amount - coalesce(ret.net_amount, 0) as net_after_returns,
            sale.partner_amount,
            coalesce(ret.partner_amount, 0) as returned_partner_amount,
            sale.partner_amount - coalesce(ret.partner_amount, 0) as partner_after_returns,
            sale.retained_amount,
            coalesce(ret.retained_amount, 0) as returned_retained_amount,
            sale.retained_amount - coalesce(ret.retained_amount, 0) as retained_after_returns,
            sale.base_net_amount - coalesce(ret.base_net_amount, 0) as base_net_after_returns,
            sale.base_partner_amount - coalesce(ret.base_partner_amount, 0)
                as base_partner_after_returns,
            sale.base_retained_amount - coalesce(ret.base_retained_amount, 0)
                as base_retained_after_returns,
            sale.recognition_journal_entry, sale.settlement_report,
            report.status as settlement_status,
            report.due_date as settlement_due_date,
            report.outstanding_amount as report_outstanding_amount,
            report.partner_credit_amount as report_partner_credit_amount
        from `tabCC Sale Allocation` sale
        left join (
            select sale_allocation,
                   sum(net_amount) as net_amount,
                   sum(partner_amount) as partner_amount,
                   sum(retained_amount) as retained_amount,
                   sum(base_net_amount) as base_net_amount,
                   sum(base_partner_amount) as base_partner_amount,
                   sum(base_retained_amount) as base_retained_amount
            from `tabCC Sale Return Allocation`
            where status = 'RETURNED'
            group by sale_allocation
        ) ret on ret.sale_allocation = sale.name
        left join `tabCC Settlement Report` report
            on report.name = sale.settlement_report and report.docstatus = 1
        inner join `tabCC Stock Lot` lot on lot.name = sale.stock_lot
        where {" and ".join(conditions)}
        order by sale.posting_datetime desc, sale.sales_invoice desc,
                 lot.received_datetime asc, sale.name asc
        """,
        tuple(values),
        as_dict=True,
    )


def get_pos_queue(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return persistent checkout, route and print state for retries and manual review."""
    import frappe

    filters = dict(filters or {})
    companies = _permitted_companies(frappe, filters.get("company"))
    if not companies:
        return []
    placeholders, company_values = _in_clause(companies)
    conditions = [f"route.company in ({placeholders})"]
    values: list[Any] = list(company_values)
    if filters.get("checkout_status"):
        conditions.append("checkout.status = %s")
        values.append(filters["checkout_status"])
    if filters.get("route_status"):
        conditions.append("route.status = %s")
        values.append(filters["route_status"])
    if filters.get("print_status"):
        conditions.append("job.status = %s")
        values.append(filters["print_status"])
    if filters.get("exceptions_only"):
        conditions.append(
            "(checkout.status in ('MANUAL_REVIEW', 'IN_PROGRESS') "
            "or route.status = 'FAILED' or job.status = 'FAILED')"
        )
    return frappe.db.sql(
        f"""
        select checkout.name as checkout, checkout.creation,
               checkout.external_order_doctype, checkout.external_order_name,
               checkout.customer, checkout.status as checkout_status,
               checkout.payment_state, checkout.currency, checkout.total_amount,
               route.name as route, route.group_id, route.company, route.location,
               route.fiscal_route, route.status as route_status,
               route.sales_invoice, route.total_amount as route_total,
               job.name as print_job, job.print_kind, job.status as print_status,
               job.attempts, job.provider_reference,
               coalesce(job.last_error, route.last_error, checkout.last_error) as last_error
        from `tabCC POS Route` route
        inner join `tabCC POS Checkout` checkout on checkout.name = route.checkout
        left join `tabCC POS Print Job` job on job.name = route.print_job
        where {" and ".join(conditions)}
        order by checkout.creation desc, route.creation asc
        """,
        tuple(values),
        as_dict=True,
    )


def get_financial_integrity_rows(company: str | None = None) -> list[dict[str, Any]]:
    from .reconciliation import audit_financial_integrity

    result = audit_financial_integrity(company=company)
    if result["issues"]:
        return result["issues"]
    return [
        {
            "code": "OK",
            "doctype": "Company" if company else "",
            "name": company or "All permitted companies",
            "message": f"No issues; checked {result['checked']}",
        }
    ]
