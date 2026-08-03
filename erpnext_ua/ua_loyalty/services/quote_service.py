from __future__ import annotations

import json
from decimal import Decimal

import frappe

from erpnext_ua.ua_loyalty.domain.allocations import RedemptionLine, allocate_redemption
from erpnext_ua.ua_loyalty.domain.money import ZERO, decimal, money, percent
from erpnext_ua.ua_loyalty.domain.snapshots import canonical_json, payload_hash, quote_envelope
from erpnext_ua.ua_loyalty.exceptions import LoyaltyError

from .account_service import account_for, current_tier
from .eligibility_service import resolve
from .settings import enabled_for
from .snapshot_service import active_snapshot


def resolve_location(
    *,
    cash_desk: str | None = None,
    branch: str | None = None,
    warehouse: str | None = None,
    at_datetime=None,
):
    filters = {"active": 1}
    if cash_desk:
        filters["pos_cash_desk"] = cash_desk
    elif branch:
        filters["branch"] = branch
    elif warehouse:
        filters["warehouse"] = warehouse
    else:
        raise LoyaltyError("Не задано контекст торгової точки", "LOYALTY_SCOPE_NOT_FOUND")
    at_datetime = frappe.utils.get_datetime(at_datetime or frappe.utils.now_datetime())
    candidates = frappe.get_all(
        "UA Loyalty Location",
        filters=filters,
        fields=["name", "scope", "valid_from", "valid_to"],
        order_by="priority asc, name asc",
        limit=100,
    )
    rows = [
        row
        for row in candidates
        if (not row.valid_from or frappe.utils.get_datetime(row.valid_from) <= at_datetime)
        and (not row.valid_to or frappe.utils.get_datetime(row.valid_to) >= at_datetime)
    ]
    if not rows:
        raise LoyaltyError("Для торгової точки не налаштовано область лояльності", "LOYALTY_SCOPE_NOT_FOUND")
    if len(rows) > 1:
        raise LoyaltyError("Торгова точка має неоднозначну область лояльності", "LOYALTY_SCOPE_AMBIGUOUS")
    return rows[0]


def quote_order(order, requested_redemption: Decimal | str | None = None) -> dict:
    if not enabled_for("POS Order"):
        return {"enabled": False, "reason_code": "LOYALTY_DISABLED"}
    location = resolve_location(cash_desk=order.cash_desk)
    account = account_for(order.customer, location.scope)
    if account.status != "ACTIVE":
        raise LoyaltyError("Рахунок лояльності заблоковано", "LOYALTY_ACCOUNT_BLOCKED")
    program = frappe.get_doc("UA Loyalty Program", account.program)
    if not program.active:
        raise LoyaltyError("Програма лояльності неактивна", "LOYALTY_PROGRAM_INACTIVE")
    snapshot = active_snapshot(program)
    tier = current_tier(account, program)
    requested = money(requested_redemption if requested_redemption is not None else order.loyalty_requested_amount)
    line_limits = []
    amounts = {}
    decisions = {}
    for row in order.items:
        amount_before = money(
            row.amount_before_loyalty
            if row.amount_before_loyalty is not None
            else decimal(row.qty) * decimal(row.rate) - decimal(row.non_loyalty_discount_amount)
        )
        earn_policy = resolve(program, "EARN", order, row)
        redeem_policy = resolve(program, "REDEEM", order, row)
        metric_policy = resolve(program, "METRIC", order, row)
        line_percent = redeem_policy.max_redemption_percent_override
        if line_percent is None:
            line_percent = decimal(program.max_redemption_percent)
        line_limit = money(amount_before * line_percent / Decimal("100")) if redeem_policy.allowed else ZERO
        amounts[row.name] = amount_before
        decisions[row.name] = (earn_policy, redeem_policy, metric_policy)
        line_limits.append(RedemptionLine(row.name, line_limit))
    aggregate_base = sum(amounts.values(), ZERO)
    program_limit = money(aggregate_base * decimal(program.max_redemption_percent) / Decimal("100"))
    maximum = min(
        money(account.redeemable_balance),
        program_limit,
        money(max(ZERO, aggregate_base - decimal(program.minimum_cash_remainder))),
    )
    allowed = min(requested, maximum)
    if requested and allowed < decimal(program.minimum_redemption_amount):
        allowed = ZERO
    allocations = {row.row: row.amount for row in allocate_redemption(allowed, line_limits)}
    items = []
    earned_total = ZERO
    metric_total = ZERO
    for row in order.items:
        earn_policy, redeem_policy, metric_policy = decisions[row.name]
        redeemed = allocations[row.name]
        cash_paid = money(amounts[row.name] - redeemed)
        earn_rate = earn_policy.earn_percent_override if earn_policy.earn_percent_override is not None else tier.rate
        extra_rate = (
            earn_policy.extra_bonus_percent
            if earn_policy.extra_bonus_percent is not None
            else decimal(program.extra_bonus_percent)
        )
        earned = percent(cash_paid, earn_rate) + percent(cash_paid, extra_rate) if earn_policy.allowed else ZERO
        metric = cash_paid if metric_policy.allowed else ZERO
        items.append(
            {
                "source_row": row.name,
                "item_code": row.item_code,
                "amount_before_loyalty": str(amounts[row.name]),
                "redeemed": str(redeemed),
                "cash_paid": str(cash_paid),
                "earn_rate": str(earn_rate),
                "extra_rate": str(extra_rate),
                "earned": str(earned),
                "metric_delta": str(metric),
                "earn_eligible": earn_policy.allowed,
                "redeem_eligible": redeem_policy.allowed,
                "metric_eligible": metric_policy.allowed,
                "reason_code": "/".join(
                    (earn_policy.reason_code, redeem_policy.reason_code, metric_policy.reason_code)
                ),
            }
        )
        earned_total += earned
        metric_total += metric
    payload = quote_envelope(
        {
            "source_doctype": "POS Order",
            "source_name": order.name,
            "customer": order.customer,
            "account": account.name,
            "account_row_version": int(account.row_version or 0),
            "scope": location.scope,
            "location": location.name,
            "program": program.name,
            "program_version": int(program.rule_version or 1),
            "snapshot_hash": snapshot.snapshot_hash,
            "metric_before": str(money(account.metric_balance)),
            "tier_code": tier.code,
            "earn_percent": str(tier.rate),
            "balance_before": str(money(account.marketing_balance)),
            "redeemable_before": str(money(account.redeemable_balance)),
            "requested_redemption": str(requested),
            "allowed_redemption": str(money(allowed)),
            "projected_earn_active": str(money(earned_total) if program.activation_mode == "IMMEDIATE" else ZERO),
            "projected_earn_pending": str(money(earned_total) if program.activation_mode != "IMMEDIATE" else ZERO),
            "projected_metric_delta": str(money(metric_total)),
            "items": items,
        }
    )
    quote_hash = payload_hash(payload)
    payload["quote_hash"] = quote_hash
    _apply_quote(order, payload)
    return payload


def _apply_quote(order, payload: dict) -> None:
    order.loyalty_scope = payload["scope"]
    order.loyalty_location = payload["location"]
    order.loyalty_program = payload["program"]
    order.loyalty_program_version = int(payload["program_version"])
    order.loyalty_snapshot_hash = payload["snapshot_hash"]
    order.loyalty_account = payload["account"]
    order.loyalty_state = "QUOTED"
    order.loyalty_quote_hash = payload["quote_hash"]
    order.loyalty_quote_json = canonical_json(payload)
    order.loyalty_metric_before = decimal(payload["metric_before"])
    order.loyalty_tier_code = payload["tier_code"]
    order.loyalty_earn_percent = decimal(payload["earn_percent"])
    order.loyalty_balance_before = money(payload["balance_before"])
    order.loyalty_redeemable_before = money(payload["redeemable_before"])
    order.loyalty_requested_amount = money(payload["requested_redemption"])
    order.loyalty_redeemed_amount = money(payload["allowed_redemption"])
    order.loyalty_earned_active = money(payload["projected_earn_active"])
    order.loyalty_earned_pending = money(payload["projected_earn_pending"])
    by_row = {row["source_row"]: row for row in payload["items"]}
    for row in order.items:
        quoted = by_row[row.name]
        row.loyalty_redeemed_amount = money(quoted["redeemed"])
        row.loyalty_earn_eligible = quoted["earn_eligible"]
        row.loyalty_redeem_eligible = quoted["redeem_eligible"]
        row.loyalty_metric_eligible = quoted["metric_eligible"]
        row.loyalty_eligibility_reason = quoted["reason_code"]
        row.loyalty_earn_base = money(quoted["cash_paid"])
        row.loyalty_metric_delta = money(quoted["metric_delta"])
        row.loyalty_earn_percent = decimal(quoted["earn_rate"])
        row.loyalty_extra_percent = decimal(quoted["extra_rate"])
        row.loyalty_earned_amount = money(quoted["earned"])
        row.loyalty_rule_snapshot = json.dumps(quoted, ensure_ascii=False, sort_keys=True)
    order.save(ignore_permissions=True)


def invalidate_order(order, reason: str = "CART_CHANGED") -> None:
    if order.loyalty_state not in {"QUOTED", "IDENTIFIED", "REQUIRES_REQUOTE"}:
        return
    order.loyalty_state = "REQUIRES_REQUOTE"
    order.loyalty_error_code = reason
    order.loyalty_quote_hash = None
    order.loyalty_quote_json = None
