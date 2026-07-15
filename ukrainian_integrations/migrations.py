from __future__ import annotations

import re

import frappe

from ukrainian_integrations.pbx_sms.vitalpbx.custom_fields import ensure_integration_custom_fields
from ukrainian_integrations.utils.operations import canonical_hash

_BANK_DESCRIPTION_PATTERN = re.compile(r"^(MBX|PBX):([^|]+?)(?:\s*\||$)")


def after_migrate() -> None:
    """Apply idempotent schema/data upgrades required by the current release."""
    ensure_integration_custom_fields()
    backfill_integration_keys()


def backfill_integration_keys() -> dict[str, int]:
    """Backfill deterministic keys without overwriting ambiguous legacy rows."""
    stats = {
        "bank_transactions": 0,
        "duplicates_skipped": 0,
    }
    stats["bank_transactions"], bank_duplicates = _backfill_bank_transaction_keys()
    stats["duplicates_skipped"] = bank_duplicates
    return stats


def _backfill_bank_transaction_keys() -> tuple[int, int]:
    account_maps = {
        "monobank": _profile_account_map("Monobank Settings"),
        "privatbank": _profile_account_map("PrivatBank Settings"),
    }
    rows = frappe.get_all(
        "Bank Transaction",
        filters={"ua_integration_key": ["is", "not set"]},
        or_filters=[
            ["description", "like", "MBX:%"],
            ["description", "like", "PBX:%"],
        ],
        fields=["name", "description", "bank_account"],
        order_by="creation asc",
    )
    updated = 0
    duplicates = 0
    seen: set[str] = set()

    for row in rows:
        match = _BANK_DESCRIPTION_PATTERN.match(row.description or "")
        if not match:
            continue
        provider = "monobank" if match.group(1) == "MBX" else "privatbank"
        tx_id = match.group(2).strip()
        mapped_accounts = account_maps[provider].get(row.bank_account, set())
        account = next(iter(mapped_accounts)) if len(mapped_accounts) == 1 else ""
        if not tx_id or not account:
            continue
        key = f"{provider}:{canonical_hash({'account': account, 'tx_id': tx_id})}"
        if key in seen or frappe.db.exists("Bank Transaction", {"ua_integration_key": key}):
            duplicates += 1
            continue
        frappe.db.set_value("Bank Transaction", row.name, "ua_integration_key", key, update_modified=False)
        seen.add(key)
        updated += 1
    return updated, duplicates


def _profile_account_map(settings_doctype: str) -> dict[str, set[str]]:
    """Map an ERP Bank Account to an unambiguous provider-side account ID."""
    if not frappe.db.exists("DocType", settings_doctype):
        return {}
    settings = frappe.get_single(settings_doctype)
    result: dict[str, set[str]] = {}
    for row in settings.get("profiles") or []:
        bank_account = (row.get("bank_account") or "").strip()
        provider_account = (row.get("account") or "").strip()
        if bank_account and provider_account:
            result.setdefault(bank_account, set()).add(provider_account)
    return result
