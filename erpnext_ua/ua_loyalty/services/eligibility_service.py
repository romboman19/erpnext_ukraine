from decimal import Decimal

import frappe

from erpnext_ua.ua_loyalty.domain.eligibility import Eligibility
from erpnext_ua.ua_loyalty.domain.money import decimal


def resolve(program, action: str, order, row) -> Eligibility:
    item = frappe.db.get_value("Item", row.item_code, ["item_group", "brand"], as_dict=True) or frappe._dict()
    rules = frappe.get_all(
        "UA Loyalty Eligibility Rule",
        filters={"program": program.name, "action": action, "active": 1},
        fields=["*"],
        order_by="priority asc, name asc",
    )
    for rule in rules:
        if _matches(rule, order, row, item):
            return Eligibility(
                rule.result == "ALLOW",
                rule.reason_code,
                _optional(rule.earn_percent_override),
                _optional(rule.extra_bonus_percent),
                _optional(rule.max_redemption_percent_override),
            )
    default = program.get(f"default_{action.lower()}_eligibility") or "ALLOW"
    return Eligibility(default == "ALLOW", "DEFAULT_ALLOWED" if default == "ALLOW" else "DEFAULT_DENIED")


def _matches(rule, order, row, item) -> bool:
    now = frappe.utils.now_datetime()
    company = frappe.db.get_value("POS Cash Desk", order.cash_desk, "company")
    customer_group = frappe.db.get_value("Customer", order.customer, "customer_group")
    checks = (
        not rule.item or rule.item == row.item_code,
        not rule.item_group or _group_matches(item.item_group, rule.item_group),
        not rule.brand or rule.brand == item.brand,
        not rule.company or rule.company == company,
        not rule.fop_profile or rule.fop_profile == row.fop_profile,
        not rule.pos_cash_desk or rule.pos_cash_desk == order.cash_desk,
        not rule.warehouse or rule.warehouse == row.warehouse,
        not rule.customer_group or rule.customer_group == customer_group,
        not rule.sales_channel or rule.sales_channel == "POS",
        not rule.manual_discount or (rule.manual_discount == "YES") == bool(decimal(row.non_loyalty_discount_amount)),
    )
    return (
        all(checks) and (not rule.valid_from or rule.valid_from <= now) and (not rule.valid_to or rule.valid_to >= now)
    )


def _group_matches(item_group: str | None, rule_group: str) -> bool:
    if not item_group:
        return False
    if item_group == rule_group:
        return True
    item_bounds = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
    rule_bounds = frappe.db.get_value("Item Group", rule_group, ["lft", "rgt"], as_dict=True)
    return bool(
        item_bounds and rule_bounds and rule_bounds.lft <= item_bounds.lft and rule_bounds.rgt >= item_bounds.rgt
    )


def _optional(value) -> Decimal | None:
    return None if value in (None, "") else decimal(value)
