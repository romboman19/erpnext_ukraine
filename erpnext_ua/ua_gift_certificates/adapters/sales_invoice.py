from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.domain.money import money
from erpnext_ua.ua_gift_certificates.services.settings import enabled_for_pos_redemption


def prepare_invoice(invoice, order):
    if not enabled_for_pos_redemption() or not order.get("gift_certificate_redeemed_total"):
        return invoice
    invoice.ua_gift_certificate_context = 1
    invoice.ua_gift_certificate_redemption_total = order.gift_certificate_redeemed_total
    invoice.ua_gift_certificate_paid_component = order.gift_certificate_paid_component
    invoice.ua_gift_certificate_promotional_component = order.gift_certificate_promotional_component
    invoice.ua_gift_certificate_snapshot = order.gift_certificate_snapshot_json
    invoice.ua_gift_certificate_posting_status = "Pending"
    _apply_accounting_dimensions(invoice)
    allocations = _quoted_allocations(order.name)
    for row in invoice.items:
        summary = allocations.get(row.get("ua_pos_order_item"), {})
        row.ua_gift_certificate_amount = summary.get("amount", 0)
        row.ua_gift_certificate_paid_component = summary.get("paid", 0)
        row.ua_gift_certificate_promotional_component = summary.get("promotional", 0)
        row.ua_gift_certificate_allocation_count = summary.get("count", 0)
        row.ua_gift_certificate_eligible = 1 if summary else 0
    from erpnext_ua.ua_gift_certificates.adapters.loyalty import apply_no_double_earning

    apply_no_double_earning(invoice)
    return invoice


def _apply_accounting_dimensions(invoice) -> None:
    profiles = {
        frappe.db.get_value("Mode of Payment", row.mode_of_payment, "ua_gift_certificate_accounting_profile")
        for row in invoice.payments
    }
    profiles.discard(None)
    cost_centers = {
        frappe.db.get_value("UA Gift Certificate Accounting Profile", profile, "default_cost_center")
        for profile in profiles
    }
    cost_centers.discard(None)
    if len(cost_centers) > 1:
        frappe.throw(
            "Gift Certificate payment components require different Cost Centers",
            title="CERT_ACCOUNTING_PROFILE_INVALID",
        )
    if profiles and not cost_centers:
        frappe.throw("Gift Certificate Cost Center is missing", title="CERT_ACCOUNTING_PROFILE_INVALID")
    if cost_centers:
        invoice.cost_center = cost_centers.pop()


def validate_before_submit(doc, method=None):
    if not doc.get("ua_gift_certificate_context"):
        return
    if not doc.get("ua_pos_order"):
        frappe.throw("Direct manual Gift Certificate redemption is disabled", title="CERT_POSTING_INCOMPLETE")
    order = frappe.get_doc("POS Order", doc.ua_pos_order)
    if doc.is_return:
        return_total = abs(money(doc.ua_gift_certificate_redemption_total))
        if return_total:
            if return_total != abs(money(order.gift_certificate_redeemed_total)):
                frappe.throw(
                    "Gift Certificate return total does not match the return plan", title="CERT_POSTING_INCOMPLETE"
                )
        return
    reserved = sum(
        row.requested_amount
        for row in frappe.get_all(
            "UA Gift Certificate Reservation",
            filters={"pos_order": order.name, "status": ("in", ["Active", "Consuming"])},
            fields=["requested_amount"],
        )
    )
    if money(reserved) != money(doc.ua_gift_certificate_redemption_total):
        frappe.throw("Gift Certificate reservation total does not match invoice", title="CERT_POSTING_INCOMPLETE")


def on_submit(doc, method=None):
    if not doc.get("ua_gift_certificate_context"):
        return
    if doc.is_return:
        from erpnext_ua.ua_gift_certificates.services.returns import restore_return_invoice

        restore_return_invoice(doc, frappe.get_doc("POS Order", doc.ua_pos_order))
        doc.db_set("ua_gift_certificate_posting_status", "Posted", update_modified=False)
        return
    from erpnext_ua.ua_gift_certificates.services.posting import consume_order

    consume_order(doc, frappe.get_doc("POS Order", doc.ua_pos_order))


def validate_before_cancel(doc, method=None):
    if not doc.get("ua_gift_certificate_context"):
        return
    if doc.is_return:
        from erpnext_ua.ua_gift_certificates.services.returns import validate_return_cancellation

        validate_return_cancellation(doc)


def on_cancel(doc, method=None):
    if not doc.get("ua_gift_certificate_context"):
        return
    from erpnext_ua.ua_gift_certificates.services.returns import reverse_invoice_certificate_effect

    reverse_invoice_certificate_effect(doc)


def _quoted_allocations(order_name: str) -> dict:
    from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money

    result = {}
    for row in frappe.get_all(
        "UA Gift Certificate Reservation",
        filters={"pos_order": order_name, "status": ("in", ["Active", "Consuming"])},
        fields=[
            "policy_snapshot_json",
            "paid_component_reserved",
            "promotional_component_reserved",
            "requested_amount",
        ],
    ):
        import json

        payload = json.loads(row.policy_snapshot_json)
        total = money(row.requested_amount)
        for allocation in payload["allocations"]:
            amount = money(allocation["amount"])
            summary = result.setdefault(allocation["row"], {"amount": 0, "paid": 0, "promotional": 0, "count": 0})
            summary["amount"] += amount
            summary["paid"] += money(amount * money(row.paid_component_reserved) / total) if total else ZERO
            summary["promotional"] += (
                money(amount * money(row.promotional_component_reserved) / total) if total else ZERO
            )
            summary["count"] += 1
    return result
