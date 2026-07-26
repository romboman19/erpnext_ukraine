from __future__ import annotations

import frappe


def ensure_telegram_customizations() -> None:
    """Install idempotent fields and v16 channel options used by Telegram."""
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    custom_fields = {
        "Notification": [
            {
                "fieldname": "ua_telegram_bot_profile",
                "fieldtype": "Link",
                "label": "Telegram Bot Profile",
                "options": "Telegram Bot Profile",
                "insert_after": "slack_webhook_url",
                "depends_on": "eval:doc.channel=='Telegram'",
                "mandatory_depends_on": "eval:doc.channel=='Telegram'",
                "no_copy": 1,
            },
            {
                "fieldname": "ua_telegram_disable_web_page_preview",
                "fieldtype": "Check",
                "label": "Disable Web Page Preview",
                "default": "1",
                "insert_after": "ua_telegram_bot_profile",
                "depends_on": "eval:doc.channel=='Telegram'",
            },
        ],
        "Notification Recipient": [
            {
                "fieldname": "ua_telegram_chat_id",
                "fieldtype": "Data",
                "label": "Telegram Chat ID",
                "insert_after": "receiver_by_document_field",
                "description": "Optional direct user, group or channel chat ID.",
            }
        ],
        "Communication": [
            {
                "fieldname": "ua_telegram_bot_profile",
                "fieldtype": "Link",
                "label": "Telegram Bot Profile",
                "options": "Telegram Bot Profile",
                "insert_after": "communication_medium",
                "read_only": 1,
                "no_copy": 1,
                "depends_on": "eval:doc.communication_medium=='Telegram'",
            }
        ],
        "User": [_party_chat_field("mobile_no")],
        "Customer": [
            _party_chat_field("mobile_no"),
            {
                "fieldname": "ua_telegram_status",
                "fieldtype": "Data",
                "label": "Telegram Status",
                "insert_after": "ua_telegram_chat_id",
                "read_only": 1,
                "no_copy": 1,
            },
        ],
        "Supplier": [_party_chat_field("mobile_no")],
        "Employee": [_party_chat_field("cell_number")],
    }
    create_custom_fields(custom_fields, update=True)
    _append_field_option("Notification", "channel", "Telegram")
    _append_field_option("Communication", "communication_medium", "Telegram")


def _party_chat_field(insert_after: str) -> dict:
    return {
        "fieldname": "ua_telegram_chat_id",
        "fieldtype": "Data",
        "label": "Telegram Chat ID",
        "insert_after": insert_after,
        "description": "Numeric chat ID confirmed for outbound Telegram notifications.",
        "no_copy": 1,
    }


def _append_field_option(doctype: str, fieldname: str, option: str) -> None:
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    standard_options = (
        frappe.db.get_value(
            "DocField",
            {"parent": doctype, "fieldname": fieldname},
            "options",
        )
        or ""
    )
    effective_field = frappe.get_meta(doctype).get_field(fieldname)
    effective_options = effective_field.options if effective_field else ""
    options = _merge_options(standard_options, effective_options, option)
    setter_name = f"{doctype}-{fieldname}-options"

    if frappe.db.exists("Property Setter", setter_name):
        setter = frappe.get_doc("Property Setter", setter_name)
        if setter.value != options:
            setter.value = options
            setter.save(ignore_permissions=True)
        return

    if option in _option_lines(effective_options):
        return
    make_property_setter(
        doctype,
        fieldname,
        "options",
        options,
        "Text",
        validate_fields_for_doctype=False,
    )


def _merge_options(*values: str) -> str:
    merged: list[str] = []
    for value in values:
        for option in _option_lines(value):
            if option not in merged:
                merged.append(option)
    return "\n".join(merged)


def _option_lines(value: str | None) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]
