from __future__ import annotations

from erpnext_ua.ua_gift_certificates.domain.money import ZERO, money


def apply_no_double_earning(invoice):
    if (
        invoice.is_return
        or not invoice.get("ua_loyalty_account")
        or not invoice.get("ua_gift_certificate_context")
    ):
        return
    pending_policy = money(invoice.get("ua_loyalty_earned_pending")) > ZERO
    earned_total = ZERO
    metric = ZERO
    for row in invoice.items:
        covered = money(row.get("ua_gift_certificate_amount"))
        old_base = money(row.get("ua_loyalty_earn_base"))
        cash_base = max(ZERO, money(old_base - covered))
        ratio = cash_base / old_base if old_base else ZERO
        old_earned = money(row.get("ua_loyalty_earned_amount"))
        old_metric = money(row.get("ua_loyalty_metric_delta"))
        row.ua_loyalty_earn_base = cash_base
        row.ua_loyalty_earned_amount = money(old_earned * ratio)
        row.ua_loyalty_metric_delta = money(old_metric * ratio)
        earned_total += row.ua_loyalty_earned_amount
        metric += row.ua_loyalty_metric_delta
    invoice.ua_loyalty_earned_active = ZERO if pending_policy else earned_total
    invoice.ua_loyalty_earned_pending = earned_total if pending_policy else ZERO
    invoice.ua_loyalty_metric_delta = metric
