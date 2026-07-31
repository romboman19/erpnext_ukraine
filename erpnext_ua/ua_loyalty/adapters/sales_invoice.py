from __future__ import annotations

from collections import defaultdict

import frappe

from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal, money
from erpnext_ua.ua_loyalty.exceptions import LoyaltyError
from erpnext_ua.ua_loyalty.services.posting_service import cancel_invoice, post_invoice
from erpnext_ua.ua_loyalty.services.settings import enabled_for


def prepare_invoice(invoice, pos_order=None) -> None:
    if not enabled_for("POS Order"):
        return
    pos_order = pos_order or (
        frappe.get_doc("POS Order", invoice.ua_pos_order) if invoice.get("ua_pos_order") else None
    )
    if not pos_order:
        return
    if invoice.is_return:
        _prepare_return(invoice, pos_order)
    elif pos_order.loyalty_account:
        _prepare_sale(invoice, pos_order)


def _header(invoice, order) -> None:
    invoice.ua_loyalty_scope = order.loyalty_scope
    invoice.ua_loyalty_location = order.loyalty_location
    invoice.ua_loyalty_program = order.loyalty_program
    invoice.ua_loyalty_program_version = order.loyalty_program_version
    invoice.ua_loyalty_account = order.loyalty_account
    invoice.ua_loyalty_snapshot_hash = order.loyalty_snapshot_hash
    invoice.ua_loyalty_quote_hash = order.loyalty_quote_hash
    invoice.ua_loyalty_reservation = order.loyalty_reservation
    invoice.ua_loyalty_snapshot_json = order.loyalty_quote_json


def _prepare_sale(invoice, order) -> None:
    _header(invoice, order)
    pos_rows = {row.name: row for row in order.items}
    invoice_groups = _invoice_rows_by_pos_row(invoice)
    for pos_name, rows in invoice_groups.items():
        pos_row = pos_rows.get(pos_name)
        if not pos_row:
            frappe.throw(f"Sales Invoice row has unknown POS source {pos_name}")
        _split_pos_values(pos_row, rows)
    invoice.ua_loyalty_redeemed_amount = money(
        sum((decimal(row.ua_loyalty_redeemed_amount) for row in invoice.items), ZERO)
    )
    invoice.ua_loyalty_earned_active = order.loyalty_earned_active
    invoice.ua_loyalty_earned_pending = order.loyalty_earned_pending
    invoice.ua_loyalty_metric_delta = money(sum((decimal(row.ua_loyalty_metric_delta) for row in invoice.items), ZERO))
    invoice.ua_loyalty_posting_key = f"sale:{invoice.name or order.name}:{order.loyalty_snapshot_hash}"


def _invoice_rows_by_pos_row(invoice) -> dict[str, list]:
    groups = defaultdict(list)
    allocation_names = [row.get("gsf_allocation") for row in invoice.items if row.get("gsf_allocation")]
    external = {}
    if allocation_names:
        external = {
            row.name: row.external_row_id
            for row in frappe.get_all(
                "GSF Allocation", filters={"name": ("in", allocation_names)}, fields=["name", "external_row_id"]
            )
        }
    for row in invoice.items:
        pos_name = row.get("ua_pos_order_item") or external.get(row.get("gsf_allocation"))
        if not pos_name:
            frappe.throw(f"Рядок Sales Invoice {row.idx} не має POS provenance")
        row.ua_pos_order_item = pos_name
        groups[pos_name].append(row)
    return groups


def _split_pos_values(pos_row, invoice_rows: list) -> None:
    total_qty = sum((abs(decimal(row.qty)) for row in invoice_rows), ZERO)
    allocated = {
        "other": ZERO,
        "redeemed": ZERO,
        "amount_before": ZERO,
        "earn_base": ZERO,
        "earned": ZERO,
        "metric": ZERO,
    }
    for index, row in enumerate(invoice_rows):
        last = index == len(invoice_rows) - 1
        share = abs(decimal(row.qty)) / total_qty if total_qty else ZERO
        values = {}
        for key, source in (
            ("other", pos_row.non_loyalty_discount_amount),
            ("redeemed", pos_row.loyalty_redeemed_amount),
            ("amount_before", pos_row.amount_before_loyalty),
            ("earn_base", pos_row.loyalty_earn_base),
            ("earned", pos_row.loyalty_earned_amount),
            ("metric", pos_row.loyalty_metric_delta),
        ):
            total = money(source)
            values[key] = money(total - allocated[key]) if last else money(total * share)
            allocated[key] += values[key]
        row.ua_loyalty_non_loyalty_discount = values["other"]
        row.ua_loyalty_redeemed_amount = values["redeemed"]
        row.ua_loyalty_amount_before = values["amount_before"]
        row.ua_loyalty_earn_base = values["earn_base"]
        row.ua_loyalty_earned_amount = values["earned"]
        row.ua_loyalty_metric_delta = values["metric"]
        row.ua_loyalty_earn_percent = pos_row.loyalty_earn_percent
        row.ua_loyalty_metric_eligible = pos_row.loyalty_metric_eligible
        row.ua_loyalty_eligibility_reason = pos_row.loyalty_eligibility_reason
        row.ua_loyalty_rule_snapshot = pos_row.loyalty_rule_snapshot


def _prepare_return(invoice, order) -> None:
    original = frappe.get_doc("Sales Invoice", invoice.return_against)
    if not original.ua_loyalty_account:
        return
    for fieldname in (
        "scope",
        "location",
        "program",
        "program_version",
        "account",
        "snapshot_hash",
        "quote_hash",
        "snapshot_json",
    ):
        invoice.set(f"ua_loyalty_{fieldname}", original.get(f"ua_loyalty_{fieldname}"))
    original_rows = {row.name: row for row in original.items}
    for row in invoice.items:
        original_row = original_rows.get(row.sales_invoice_item)
        if not original_row:
            frappe.throw("Повернення не має точного зв’язку з первинним рядком", title="LOYALTY_RETURN_LINK_REQUIRED")
        share = abs(decimal(row.qty)) / abs(decimal(original_row.qty))
        row.ua_pos_order_item = original_row.ua_pos_order_item
        row.ua_loyalty_original_invoice_item = original_row.name
        row.ua_loyalty_original_order_item = original_row.ua_pos_order_item
        row.ua_loyalty_non_loyalty_discount = money(decimal(original_row.ua_loyalty_non_loyalty_discount) * share)
        row.ua_loyalty_redeemed_amount = money(decimal(original_row.ua_loyalty_redeemed_amount) * share)
        row.ua_loyalty_amount_before = money(decimal(original_row.ua_loyalty_amount_before) * share)
        row.ua_loyalty_earn_base = money(decimal(original_row.ua_loyalty_earn_base) * share)
        row.ua_loyalty_earned_amount = money(decimal(original_row.ua_loyalty_earned_amount) * share)
        row.ua_loyalty_metric_delta = money(decimal(original_row.ua_loyalty_metric_delta) * share)
        row.ua_loyalty_earn_percent = original_row.ua_loyalty_earn_percent
        row.ua_loyalty_metric_eligible = original_row.ua_loyalty_metric_eligible
        row.ua_loyalty_eligibility_reason = original_row.ua_loyalty_eligibility_reason
        row.ua_loyalty_rule_snapshot = original_row.ua_loyalty_rule_snapshot
    invoice.ua_loyalty_redeemed_amount = money(
        sum((decimal(row.ua_loyalty_redeemed_amount) for row in invoice.items), ZERO)
    )
    invoice.ua_loyalty_posting_key = f"return:{invoice.name or order.name}:{original.ua_loyalty_snapshot_hash}"


def validate_before_submit(doc, method=None):
    del method
    if doc.get("ua_pos_order"):
        prepare_invoice(doc)
    if not doc.get("ua_loyalty_account"):
        return
    standard_values = (
        doc.get("redeem_loyalty_points"),
        doc.get("loyalty_points"),
        doc.get("loyalty_amount"),
        doc.get("loyalty_program"),
    )
    if any(standard_values):
        frappe.throw("Стандартну та UA Loyalty не можна проводити одночасно", title="LOYALTY_STANDARD_CONFLICT")
    if not doc.ua_loyalty_snapshot_hash or not doc.ua_loyalty_snapshot_json:
        frappe.throw("Документ не має immutable loyalty snapshot", title="LOYALTY_QUOTE_HASH_MISMATCH")
    if doc.is_return and (not doc.return_against or any(not row.sales_invoice_item for row in doc.items)):
        frappe.throw("Повернення потребує точного primary row linkage", title="LOYALTY_RETURN_LINK_REQUIRED")


def on_submit(doc, method=None):
    del method
    if doc.get("ua_loyalty_account") and enabled_for("POS Order" if doc.get("ua_pos_order") else "Sales Invoice"):
        try:
            post_invoice(doc)
        except LoyaltyError as error:
            frappe.throw(str(error), title=error.code)


def validate_before_cancel(doc, method=None):
    del method
    if doc.get("ua_loyalty_account") and not doc.get("ua_loyalty_posted"):
        frappe.throw("Loyalty posting первинного документа відсутній", title="LOYALTY_RECONCILIATION_MISMATCH")


def on_cancel(doc, method=None):
    del method
    if doc.get("ua_loyalty_account"):
        cancel_invoice(doc)
