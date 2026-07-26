import string

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.integrations.communication.telegram.profile import get_enabled_bot_profile
from erpnext_ua.integrations.pbx_sms.sms.turbosms import (
    _get_turbosms_settings,
    configured_sender_names,
    resolve_configured_sender,
)


class CustomerIdentificationSettings(Document):
    def validate(self):
        self.default_channel = self.default_channel or "SMS"
        self.pos_channel = self.pos_channel or "SMS"

        for fieldname, minimum, maximum, label in (
            ("ttl_minutes", 1, 60, _("Строк дії запиту")),
            ("max_attempts", 1, 10, _("Максимум спроб коду")),
            ("rate_limit_per_10_min", 1, 50, _("Ліміт запитів")),
        ):
            value = int(self.get(fieldname) or 0)
            if value < minimum or value > maximum:
                frappe.throw(
                    _("{0}: значення має бути від {1} до {2}").format(
                        label,
                        minimum,
                        maximum,
                    )
                )

        channel_fields = {
            "SMS": "sms_enabled",
            "Telegram": "telegram_enabled",
            "Call": "call_enabled",
        }
        enabled_channels = {
            channel
            for channel, fieldname in channel_fields.items()
            if self.get(fieldname)
        }
        if self.enabled and not enabled_channels:
            frappe.throw(_("Увімкніть хоча б один канал ідентифікації"))
        if self.enabled:
            for fieldname, label in (
                ("default_channel", _("Типовий канал")),
                ("pos_channel", _("Канал каси POS")),
            ):
                channel = self.get(fieldname)
                if channel not in channel_fields:
                    frappe.throw(_("Невідомий канал у полі {0}").format(label))
                if channel not in enabled_channels:
                    frappe.throw(_("Канал {0} має бути увімкнений").format(channel))

        if self.sms_enabled:
            template = str(self.sms_template or "")
            fields = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(template)
                if field_name
            }
            if not template or not {"code", "minutes"}.issubset(fields):
                frappe.throw(
                    _("Шаблон SMS має містити {code} та {minutes}")
                )
            if not self.test_mode:
                turbosms = _get_turbosms_settings()
                if not int(turbosms.get("enabled") or 0):
                    frappe.throw(_("TurboSMS integration must be enabled for SMS identification"))
                if not configured_sender_names(turbosms):
                    frappe.throw(_("Add at least one active sender in TurboSMS Settings"))
                try:
                    self.sms_sender = resolve_configured_sender(self.sms_sender or None, turbosms)
                except ValueError as exc:
                    frappe.throw(_(str(exc)))

        if self.telegram_enabled:
            profile = get_enabled_bot_profile(self.telegram_bot_profile)
            if not str(profile.bot_username or "").strip():
                frappe.throw(_("У вибраному профілі немає username Telegram-бота"))
            if not self.get_password(
                "telegram_webhook_secret",
                raise_exception=False,
            ):
                frappe.throw(_("Потрібно вказати секрет webhook Telegram"))

            ttl = int(self.telegram_ttl_minutes or 0)
            if ttl < 1 or ttl > 15:
                frappe.throw(
                    _("Хвилин на push-підтвердження: значення має бути від 1 до 15")
                )
            for fieldname, label in (
                ("telegram_confirm_button", _("Текст кнопки підтвердження")),
                ("telegram_cancel_button", _("Текст кнопки скасування")),
            ):
                if not str(self.get(fieldname) or "").strip():
                    frappe.throw(_("{0}: поле не може бути порожнім").format(label))
            for fieldname, label, required_placeholders in (
                (
                    "telegram_confirm_template",
                    _("Шаблон push-підтвердження"),
                    {"phone"},
                ),
                (
                    "telegram_linked_template",
                    _("Шаблон успішного підключення"),
                    {"phone"},
                ),
                (
                    "telegram_unmatched_template",
                    _("Шаблон невідомого номера"),
                    {"phone"},
                ),
                (
                    "telegram_consent_template",
                    _("Шаблон згоди на обробку контакту"),
                    set(),
                ),
                (
                    "telegram_welcome_template",
                    _("Шаблон вітання /start"),
                    set(),
                ),
            ):
                template = str(self.get(fieldname) or "").strip()
                if not template:
                    frappe.throw(_("{0}: шаблон не може бути порожнім").format(label))
                placeholders = {
                    field_name
                    for _, field_name, _, _ in string.Formatter().parse(template)
                    if field_name
                }
                if required_placeholders and not required_placeholders.issubset(placeholders):
                    frappe.throw(
                        _("{0}: шаблон має містити {1}").format(
                            label,
                            ", ".join(f"{{{p}}}" for p in sorted(required_placeholders)),
                        )
                    )

        if self.call_enabled and not str(
            self.call_verification_number or ""
        ).strip():
            frappe.throw(_("Потрібно вказати номер для контрольного дзвінка"))
