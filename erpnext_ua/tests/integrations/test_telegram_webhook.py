from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

# Pure-logic tests elsewhere in the suite install a stub `frappe` module, so a
# successful `import frappe` is not proof of a test site. Importing the
# site-only parts inside the same `try` keeps the skip working either way.
try:
    import frappe
    from frappe.tests.utils import FrappeTestCase

    from erpnext_ua.integrations.customer_identification.telegram import webhook
except ModuleNotFoundError:
    frappe = None
    FrappeTestCase = unittest.TestCase


class _FakeSettings:
    enabled = 1
    telegram_enabled = 1
    telegram_bot_username = "test_bot"
    telegram_welcome_template = "Welcome!"
    telegram_consent_template = "Consent text."
    telegram_linked_template = "Linked {phone}."
    telegram_unmatched_template = "Unmatched {phone}."
    telegram_confirm_template = "Confirm {phone}."
    telegram_confirm_button = "Confirm"
    telegram_cancel_button = "Cancel"
    telegram_ttl_minutes = 3

    def get_password(self, fieldname, raise_exception=False):
        return "dummy"


class _MockRequest:
    def __init__(self, payload: dict):
        self._payload = payload
        self.content_length = len(json.dumps(payload).encode("utf-8"))

    def get_json(self, silent=False):
        return self._payload


@unittest.skipIf(frappe is None, "requires a Frappe test site")
class TelegramWebhookTest(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self._telegram_calls: list[tuple[str, dict]] = []
        self._validate_secret_patcher = patch(
            "erpnext_ua.integrations.customer_identification.telegram._validate_secret"
        )
        self._validate_secret_patcher.start()
        self._settings_patcher = patch(
            "erpnext_ua.integrations.customer_identification.telegram._settings",
            return_value=_FakeSettings(),
        )
        self._settings_patcher.start()
        self._telegram_patcher = patch(
            "erpnext_ua.integrations.customer_identification.telegram._telegram",
            side_effect=self._record_telegram_call,
        )
        self._telegram_patcher.start()

    def tearDown(self):
        self._telegram_patcher.stop()
        self._settings_patcher.stop()
        self._validate_secret_patcher.stop()
        super().tearDown()

    def _record_telegram_call(self, method: str, payload: dict) -> dict:
        self._telegram_calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 1}}

    def _call_webhook(self, payload: dict) -> dict:
        frappe.request = _MockRequest(payload)
        frappe.local.response = {}
        return webhook()

    def _unique_chat_id(self) -> str:
        return frappe.utils.now_datetime().strftime("%Y%m%d%H%M%S%f")

    def _new_customer(self, phone: str = "+380501112233") -> str:
        doc = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": "Test Telegram Customer",
                "customer_type": "Individual",
                "customer_group": "Individual",
                "territory": "Ukraine",
                "mobile_no": phone,
            }
        )
        doc.insert(ignore_permissions=True)
        return doc.name

    def test_start_sends_welcome_with_contact_keyboard(self):
        chat_id = self._unique_chat_id()
        self._call_webhook({"message": {"chat": {"id": chat_id}, "text": "/start"}})
        send_calls = [c for c in self._telegram_calls if c[0] == "sendMessage"]
        self.assertTrue(send_calls)
        payload = send_calls[0][1]
        self.assertIn("Welcome", payload["text"])
        self.assertIn("keyboard", payload["reply_markup"])
        self.assertTrue(payload["reply_markup"]["keyboard"][0][0]["request_contact"])

    def test_contact_links_existing_customer(self):
        phone = "+380509998877"
        self._new_customer(phone)
        chat_id = self._unique_chat_id()
        self._call_webhook(
            {
                "message": {
                    "chat": {"id": chat_id},
                    "from": {"id": 12345},
                    "contact": {"phone_number": phone, "user_id": 12345},
                }
            }
        )
        send_calls = [c for c in self._telegram_calls if c[0] == "sendMessage"]
        self.assertTrue(send_calls)
        payload = send_calls[0][1]
        self.assertIn("Linked", payload["text"])
        link = frappe.db.get_value(
            "Customer Telegram Link",
            {"chat_id": chat_id},
            "status",
        )
        self.assertEqual(link, "Active")

    def test_contact_for_unknown_phone_sends_unmatched_template(self):
        chat_id = self._unique_chat_id()
        self._call_webhook(
            {
                "message": {
                    "chat": {"id": chat_id},
                    "from": {"id": 12345},
                    "contact": {"phone_number": "+380500000000", "user_id": 12345},
                }
            }
        )
        send_calls = [c for c in self._telegram_calls if c[0] == "sendMessage"]
        self.assertTrue(send_calls)
        payload = send_calls[0][1]
        self.assertIn("Unmatched", payload["text"])

    def test_stop_stops_active_link(self):
        from erpnext_ua.integrations.customer_identification.telegram_link import (
            ensure_telegram_link,
        )

        chat_id = self._unique_chat_id()
        customer = self._new_customer()
        ensure_telegram_link(customer=customer, phone="+380501112233", chat_id=chat_id)
        self._telegram_calls.clear()
        self._call_webhook({"message": {"chat": {"id": chat_id}, "text": "/stop"}})
        send_calls = [c for c in self._telegram_calls if c[0] == "sendMessage"]
        self.assertTrue(send_calls)
        status = frappe.db.get_value(
            "Customer Telegram Link",
            {"chat_id": chat_id},
            "status",
        )
        self.assertEqual(status, "Stopped")

    def test_status_reports_link_state(self):
        from erpnext_ua.integrations.customer_identification.telegram_link import (
            ensure_telegram_link,
        )

        chat_id = self._unique_chat_id()
        customer = self._new_customer()
        ensure_telegram_link(customer=customer, phone="+380501112233", chat_id=chat_id)
        self._telegram_calls.clear()
        self._call_webhook({"message": {"chat": {"id": chat_id}, "text": "/status"}})
        send_calls = [c for c in self._telegram_calls if c[0] == "sendMessage"]
        self.assertTrue(send_calls)
        payload = send_calls[0][1]
        self.assertIn("активна", payload["text"])

    def test_callback_query_is_acked(self):
        chat_id = self._unique_chat_id()
        self._call_webhook(
            {
                "callback_query": {
                    "id": "cq_123",
                    "from": {"id": chat_id},
                    "data": "cid_token",
                }
            }
        )
        answer_calls = [c for c in self._telegram_calls if c[0] == "answerCallbackQuery"]
        self.assertTrue(answer_calls)
        self.assertEqual(answer_calls[0][1]["callback_query_id"], "cq_123")


if __name__ == "__main__":
    unittest.main()
