from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.integrations.communication.telegram.client import is_valid_bot_token


class TelegramBotProfile(Document):
    def validate(self) -> None:
        self.profile_name = str(self.profile_name or "").strip()
        self.bot_username = str(self.bot_username or "").strip().removeprefix("@")
        if self.bot_username and not re.fullmatch(r"[A-Za-z0-9_]{5,32}", self.bot_username):
            frappe.throw(_("Telegram bot username must contain 5-32 letters, digits or underscores"))

        maximum = int(self.max_message_length or 0)
        if maximum < 1 or maximum > 4096:
            frappe.throw(_("Maximum Telegram message length must be between 1 and 4096"))

        if not self.enabled:
            return
        token = (self.get_password("bot_token", raise_exception=False) or "").strip()
        if not token:
            frappe.throw(_("Enabled Telegram bot profile requires a token"))
        if not is_valid_bot_token(token):
            frappe.throw(_("Telegram bot token has an invalid format"))
