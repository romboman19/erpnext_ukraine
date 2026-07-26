from __future__ import annotations

import frappe
from frappe import _


def get_enabled_bot_profile(name: str | None):
    profile_name = str(name or '').strip()
    if not profile_name:
        frappe.throw(_('Select a Telegram Bot Profile'))

    profile = frappe.get_doc('Telegram Bot Profile', profile_name)
    if not profile.enabled:
        frappe.throw(_('Selected Telegram Bot Profile is disabled'))
    if not profile.get_password('bot_token', raise_exception=False):
        frappe.throw(_('Selected Telegram Bot Profile has no token'))
    return profile
