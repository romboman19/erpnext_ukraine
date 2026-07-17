from __future__ import annotations

import json
import re

import frappe

from ukrainian_integrations.pbx_sms.vitalpbx.custom_fields import ensure_integration_custom_fields
from ukrainian_integrations.utils.operations import canonical_hash

_BANK_DESCRIPTION_PATTERN = re.compile(r"^(MBX|PBX):([^|]+?)(?:\s*\||$)")


def after_migrate() -> None:
    """Apply idempotent schema/data upgrades required by the current release."""
    ensure_integration_custom_fields()
    ensure_identification_channel_defaults()
    backfill_integration_keys()
    remove_legacy_integration_artifacts()
    refresh_desk_navigation()


_DESK_APP_ICON_FIELDS = (
    "label",
    "bg_color",
    "link",
    "link_type",
    "app",
    "icon_type",
    "parent_icon",
    "icon",
    "link_to",
    "idx",
    "standard",
    "logo_url",
    "hidden",
    "name",
    "restrict_removal",
    "icon_image",
)
_CUSTOM_DESK_APPS = ("ukrainian_integrations", "erpnext_ua", "print_designer")
_LEGACY_INTEGRATION_DOCTYPES = ("NP Integration Settings", "UP Integration Settings")


def remove_legacy_integration_artifacts() -> dict[str, object]:
    """Remove settings superseded by sender profiles and the TurboSMS sender table."""
    removed_doctypes = []
    for doctype in _LEGACY_INTEGRATION_DOCTYPES:
        if not frappe.db.exists("DocType", doctype):
            continue
        frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
        removed_doctypes.append(doctype)

    removed_turbosms_field = False
    migrated_turbosms_sender = False
    if frappe.db.exists("DocType", "TurboSMS Settings"):
        # Schema sync removes the standard DocField before after_migrate runs,
        # while the legacy value can still be present in tabSingles. Reading
        # through get_single_value() would therefore fail metadata validation.
        legacy_rows = frappe.db.sql(
            """
            SELECT `value`
            FROM `tabSingles`
            WHERE `doctype` = %s AND `field` = %s
            LIMIT 1
            """,
            ("TurboSMS Settings", "sender"),
        )
        legacy_sender = (legacy_rows[0][0] if legacy_rows else "").strip()
        settings = frappe.get_single("TurboSMS Settings")
        if legacy_sender and not settings.get("senders"):
            settings.append(
                "senders",
                {"sender_name": legacy_sender, "is_active": 1, "is_default": 1},
            )
            settings.save(ignore_permissions=True)
            migrated_turbosms_sender = True
        frappe.db.delete("Singles", {"doctype": "TurboSMS Settings", "field": "sender"})
        if frappe.db.get_value("DocType", "TurboSMS Settings", "custom"):
            settings_meta = frappe.get_doc("DocType", "TurboSMS Settings")
            legacy_fields = [field for field in settings_meta.fields if field.fieldname == "sender"]
            for field in legacy_fields:
                settings_meta.remove(field)
            if legacy_fields:
                settings_meta.save(ignore_permissions=True)
                removed_turbosms_field = True
        frappe.clear_cache(doctype="TurboSMS Settings")

    return {
        "removed_doctypes": removed_doctypes,
        "removed_turbosms_field": removed_turbosms_field,
        "migrated_turbosms_sender": migrated_turbosms_sender,
    }


def ensure_identification_channel_defaults() -> dict[str, str]:
    """Backfill channel routing without enabling provider traffic."""
    doctype = "Customer Identification Settings"
    if not frappe.db.exists("DocType", doctype):
        return {}
    updated = {}
    for fieldname in ("default_channel", "pos_channel"):
        if frappe.db.get_single_value(doctype, fieldname):
            continue
        frappe.db.set_single_value(doctype, fieldname, "SMS")
        updated[fieldname] = "SMS"
    return updated


def _merge_app_icons_into_layout(
    layout: list[dict], app_icons: list[dict]
) -> tuple[list[dict], int]:
    """Preserve a user's layout while adding applications installed later."""
    merged = [dict(icon) for icon in layout]
    existing_labels = {icon.get("label") for icon in merged}
    added = 0
    for icon in app_icons:
        if icon.get("label") in existing_labels:
            continue
        merged.append(dict(icon))
        existing_labels.add(icon.get("label"))
        added += 1
    return merged, added


def refresh_desk_navigation() -> dict[str, int]:
    """Make new custom applications visible without resetting saved user layouts."""
    from frappe.desk.doctype.desktop_icon.desktop_icon import create_desktop_icons

    create_desktop_icons()
    app_icons = frappe.get_all(
        "Desktop Icon",
        filters={
            "app": ["in", _CUSTOM_DESK_APPS],
            "icon_type": "App",
            "hidden": 0,
            "standard": 1,
        },
        fields=list(_DESK_APP_ICON_FIELDS),
        order_by="idx asc",
    )

    updated_layouts = 0
    added_icons = 0
    for row in frappe.get_all("Desktop Layout", fields=["name", "layout"]):
        try:
            layout = json.loads(row.layout or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(layout, list):
            continue
        merged, added = _merge_app_icons_into_layout(layout, app_icons)
        if not added:
            continue
        frappe.db.set_value(
            "Desktop Layout",
            row.name,
            "layout",
            json.dumps(merged, ensure_ascii=False),
            update_modified=False,
        )
        updated_layouts += 1
        added_icons += added

    # Both are Redis hashes keyed by user. Deleting the hashes refreshes every
    # user, unlike clear_desktop_icons_cache(), which only clears one user.
    frappe.cache.delete_value(("desktop_icons", "bootinfo"))
    return {"updated_layouts": updated_layouts, "added_icons": added_icons}


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
