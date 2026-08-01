from __future__ import annotations

import unittest

# Pure-logic tests elsewhere in the suite install a stub `frappe` module, so a
# successful `import frappe` is not proof of a test site. Importing the
# site-only parts inside the same `try` keeps the skip working either way.
try:
    import frappe
    from frappe.tests.utils import FrappeTestCase

    from erpnext_ua.integrations.customer_identification.telegram_link import (
        ensure_telegram_link,
        get_active_link_for_customer,
        get_link_by_chat_id,
        on_customer_insert,
        record_verification,
        stop_link,
    )
    from erpnext_ua.tests.integrations.frappe_fixtures import ensure_customer_master_links
except ModuleNotFoundError:
    frappe = None
    FrappeTestCase = unittest.TestCase


def _unique_chat_id() -> str:
    """Return a numeric Telegram chat ID (Telegram chat IDs are integers)."""
    return frappe.utils.now_datetime().strftime("%Y%m%d%H%M%S%f")


@unittest.skipIf(frappe is None, "requires a Frappe test site")
class CustomerTelegramLinkTest(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self.customer_group, self.territory = ensure_customer_master_links()

    def _new_customer(self, phone: str = "+380501112233"):
        doc = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": "Test Telegram Customer",
                "customer_type": "Individual",
                "customer_group": self.customer_group,
                "territory": self.territory,
                "mobile_no": phone,
            }
        )
        doc.insert(ignore_permissions=True)
        return doc.name

    def test_ensure_telegram_link_creates_and_updates(self):
        customer = self._new_customer()
        chat_id = _unique_chat_id()
        result = ensure_telegram_link(
            customer=customer,
            phone="+380501112233",
            chat_id=chat_id,
            telegram_user_id=987654321,
        )
        self.assertEqual(result["status"], "Active")
        self.assertEqual(result["chat_id"], chat_id)
        self.assertEqual(result["verification_count"], 0)

        # Second call for the same customer updates the same record.
        new_chat_id = _unique_chat_id()
        updated = ensure_telegram_link(
            customer=customer,
            phone="+380509998877",
            chat_id=new_chat_id,
            telegram_user_id="111222333",
        )
        self.assertEqual(updated["chat_id"], new_chat_id)
        self.assertEqual(
            get_active_link_for_customer(customer).name,
            result["name"],
        )

    def test_chat_id_cannot_be_linked_to_two_customers(self):
        customer_a = self._new_customer("+380501110000")
        customer_b = self._new_customer("+380502220000")
        chat_id = _unique_chat_id()
        ensure_telegram_link(
            customer=customer_a,
            phone="+380501110000",
            chat_id=chat_id,
        )
        with self.assertRaises(frappe.ValidationError):
            ensure_telegram_link(
                customer=customer_b,
                phone="+380502220000",
                chat_id=chat_id,
            )

    def test_stop_link_and_record_verification(self):
        customer = self._new_customer()
        chat_id = _unique_chat_id()
        ensure_telegram_link(
            customer=customer,
            phone="+380501112233",
            chat_id=chat_id,
        )
        record_verification(chat_id)
        link = get_link_by_chat_id(chat_id)
        self.assertEqual(int(link.verification_count), 1)
        self.assertIsNotNone(link.last_verified_at)

        stop_link(chat_id, reason="Customer opted out")
        link = get_link_by_chat_id(chat_id)
        self.assertEqual(link.status, "Stopped")
        self.assertIn("Customer opted out", link.stop_reason)

    def test_on_customer_insert_creates_link_from_legacy_field(self):
        chat_id = _unique_chat_id()
        customer = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": "Legacy Telegram Customer",
                "customer_type": "Individual",
                "customer_group": self.customer_group,
                "territory": self.territory,
                "mobile_no": "+380503332211",
                "ua_telegram_chat_id": chat_id,
            }
        )
        customer.insert(ignore_permissions=True)
        on_customer_insert(customer)

        link = get_active_link_for_customer(customer.name)
        self.assertIsNotNone(link)
        self.assertEqual(link.chat_id, chat_id)
        self.assertEqual(link.phone, "+380503332211")

    def test_customer_telegram_status_is_written(self):
        if not frappe.get_meta("Customer").has_field("ua_telegram_status"):
            self.skipTest("Customer.ua_telegram_status field is not installed")
        customer = self._new_customer()
        chat_id = _unique_chat_id()
        ensure_telegram_link(
            customer=customer,
            phone="+380501112233",
            chat_id=chat_id,
        )
        status = frappe.db.get_value("Customer", customer, "ua_telegram_status")
        self.assertEqual(status, "Active")

        stop_link(chat_id)
        status = frappe.db.get_value("Customer", customer, "ua_telegram_status")
        self.assertEqual(status, "Stopped")
