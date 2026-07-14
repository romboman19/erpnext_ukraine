import unittest
from unittest.mock import patch

from erpnext_ua.ua_pos.api import _reconcile_existing_receipt


class TestFiscalRecovery(unittest.TestCase):
	@patch("erpnext_ua.ua_fiscal.orchestration.reconcile_receipt")
	@patch("erpnext_ua.ua_pos.api.frappe.db.get_value", return_value="Uncertain")
	def test_uncertain_receipt_is_reconciled_before_retry(self, _get_value, reconcile):
		reconcile.return_value = {"name": "RECEIPT-1", "status": "Fiscalized"}

		result = _reconcile_existing_receipt("RECEIPT-1")

		reconcile.assert_called_once_with("RECEIPT-1")
		self.assertEqual(result["status"], "Fiscalized")

	@patch("erpnext_ua.ua_fiscal.orchestration.reconcile_receipt")
	@patch("erpnext_ua.ua_pos.api.frappe.db.get_value", return_value="Error")
	def test_automatic_recovery_does_not_resend_definite_error(self, _get_value, reconcile):
		result = _reconcile_existing_receipt("RECEIPT-2")

		reconcile.assert_not_called()
		self.assertEqual(result["status"], "Error")

	@patch("erpnext_ua.ua_pos.api.frappe.db.get_value", return_value="Fiscalized")
	def test_confirmed_receipt_is_idempotent(self, _get_value):
		self.assertEqual(
			_reconcile_existing_receipt("RECEIPT-3"),
			{"name": "RECEIPT-3", "status": "Fiscalized"},
		)


if __name__ == "__main__":
	unittest.main()
