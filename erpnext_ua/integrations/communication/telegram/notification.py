from __future__ import annotations

import frappe
from frappe import _
from frappe.utils.jinja import validate_template

from erpnext_ua.integrations.communication.telegram.client import is_valid_chat_id
from erpnext_ua.integrations.communication.telegram.service import queue_notification_deliveries


class TelegramNotificationMixin:
    """Add Telegram dispatch to Frappe v16's standard Notification controller."""

    def validate(self) -> None:
        super().validate()
        if self.channel != "Telegram":
            return

        if not self.subject:
            frappe.throw(_("Subject is required for Telegram notifications"))
        validate_template(self.subject)
        if not self.ua_telegram_bot_profile:
            frappe.throw(_("Telegram Bot Profile is required"))

        profile = frappe.get_doc("Telegram Bot Profile", self.ua_telegram_bot_profile)
        if not profile.enabled:
            frappe.throw(_("Selected Telegram Bot Profile is disabled"))
        if not profile.get_password("bot_token", raise_exception=False):
            frappe.throw(_("Selected Telegram Bot Profile has no token"))
        if self.attach_files:
            frappe.throw(
                _("Telegram notifications support Attach Print only; file-field and all-file attachments are disabled")
            )

        has_recipient = bool(self.send_to_all_assignees)
        for recipient in self.recipients or []:
            direct_chat_id = str(recipient.get("ua_telegram_chat_id") or "").strip()
            if direct_chat_id and not is_valid_chat_id(direct_chat_id):
                frappe.throw(_("Invalid direct Telegram Chat ID in recipient row {0}").format(recipient.idx))
            has_recipient = has_recipient or bool(
                direct_chat_id or recipient.receiver_by_document_field or recipient.receiver_by_role
            )
        if not has_recipient:
            frappe.throw(_("Configure at least one Telegram recipient"))

    def send_notification_by_channel(self, doc, context) -> None:
        if self.channel != "Telegram":
            return super().send_notification_by_channel(doc, context)

        try:
            queue_notification_deliveries(self, doc, context)
            if self.send_system_notification:
                self.create_system_notification(doc, context)
        except Exception:
            self.log_error("Failed to queue Telegram Notification")
