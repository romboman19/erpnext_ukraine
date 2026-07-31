from __future__ import annotations

import frappe

from erpnext_ua import __version__
from erpnext_ua.ua_loyalty.context import service_write
from erpnext_ua.ua_loyalty.domain.snapshots import canonical_json, payload_hash


def program_payload(program) -> dict:
    rules = frappe.get_all(
        "UA Loyalty Eligibility Rule",
        filters={"program": program.name, "active": 1},
        fields=["*"],
        order_by="priority asc, name asc",
    )
    return {
        "program": program.name,
        "scope": program.scope,
        "version": int(program.rule_version or 1),
        "rate_timing": program.rate_timing,
        "credit_consumption_mode": program.credit_consumption_mode,
        "expiry_writeoff_mode": program.expiry_writeoff_mode,
        "amount_base_mode": program.amount_base_mode,
        "max_redemption_percent": str(program.max_redemption_percent or 0),
        "minimum_redemption_amount": str(program.minimum_redemption_amount or 0),
        "minimum_cash_remainder": str(program.minimum_cash_remainder or 0),
        "require_one_non_bonus_item": int(program.require_one_non_bonus_item or 0),
        "activation_mode": program.activation_mode,
        "activation_days": int(program.activation_days or 0),
        "expiry_mode": program.expiry_mode,
        "bonus_validity_days": int(program.bonus_validity_days or 0),
        "returned_bonus_validity_days": int(program.returned_bonus_validity_days or 0),
        "extra_bonus_percent": str(program.extra_bonus_percent or 0),
        "default_earn_eligibility": program.default_earn_eligibility,
        "default_redeem_eligibility": program.default_redeem_eligibility,
        "default_metric_eligibility": program.default_metric_eligibility,
        "tiers": [
            {
                "code": row.tier_code,
                "name": row.tier_name,
                "threshold": str(row.threshold_amount),
                "rate": str(row.earn_percent),
            }
            for row in sorted(program.tiers, key=lambda item: item.threshold_amount)
        ],
        "eligibility_rules": [
            {
                key: str(value) if value is not None else None
                for key, value in row.items()
                if key
                not in {
                    "creation",
                    "modified",
                    "owner",
                    "modified_by",
                    "_user_tags",
                    "_comments",
                    "_assign",
                    "_liked_by",
                }
            }
            for row in rules
        ],
    }


def publish(program_name: str):
    program = frappe.get_doc("UA Loyalty Program", program_name)
    payload = program_payload(program)
    fingerprint = payload_hash(payload)
    existing = frappe.db.get_value("UA Loyalty Rule Snapshot", {"snapshot_hash": fingerprint}, "name")
    if existing:
        return frappe.get_doc("UA Loyalty Rule Snapshot", existing)
    with service_write():
        snapshot = frappe.get_doc(
            {
                "doctype": "UA Loyalty Rule Snapshot",
                "program": program.name,
                "version": int(program.rule_version or 1),
                "published_at": frappe.utils.now_datetime(),
                "published_by": frappe.session.user,
                "effective_from": frappe.utils.now_datetime(),
                "snapshot_json": canonical_json(payload),
                "snapshot_hash": fingerprint,
                "app_version": __version__,
                "status": "ACTIVE",
            }
        ).insert(ignore_permissions=True)
        program.published_snapshot_hash = fingerprint
        program.save(ignore_permissions=True)
    return snapshot


def active_snapshot(program):
    if not program.published_snapshot_hash:
        return publish(program.name)
    name = frappe.db.get_value(
        "UA Loyalty Rule Snapshot", {"snapshot_hash": program.published_snapshot_hash, "status": "ACTIVE"}, "name"
    )
    if not name:
        frappe.throw("Для програми немає активного опублікованого snapshot")
    return frappe.get_doc("UA Loyalty Rule Snapshot", name)
