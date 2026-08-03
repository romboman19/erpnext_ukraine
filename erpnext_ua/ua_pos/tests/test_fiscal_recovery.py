import json
import types
import unittest
from unittest.mock import patch

from erpnext_ua.ua_pos.api import (
	_apply_route_receipt_state,
	_fiscalize_routes,
	_reconcile_existing_receipt,
	_route_receipt_state,
)


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

	@patch("erpnext_ua.ua_pos.api._fiscalize", side_effect=["RECEIPT-A", "RECEIPT-B"])
	@patch("erpnext_ua.ua_pos.api.frappe.get_doc")
	def test_each_fiscal_legal_route_gets_its_own_receipt(self, get_doc, fiscalize):
		get_doc.side_effect = lambda _doctype, name: types.SimpleNamespace(name=name)
		order = types.SimpleNamespace(fiscal_mode="Fiscal")
		routes = [
			_route("FISCAL", "DESK-A", "SINV-A"),
			_route("NON_FISCAL", "DESK-N", "SINV-N"),
			_route("FISCAL", "DESK-B", "SINV-B"),
		]

		receipts = _fiscalize_routes(order, routes)

		self.assertEqual(receipts, ["RECEIPT-A", "RECEIPT-B"])
		self.assertEqual(
			[(call.args[1].name, call.args[2].name) for call in fiscalize.call_args_list],
			[("DESK-A", "SINV-A"), ("DESK-B", "SINV-B")],
		)

	@patch("erpnext_ua.ua_pos.api.frappe.db.get_value")
	def test_route_receipt_state_waits_for_every_fiscal_invoice(self, get_value):
		get_value.side_effect = [
			types.SimpleNamespace(name="RECEIPT-A", status="Fiscalized"),
			types.SimpleNamespace(name="RECEIPT-B", status="Uncertain"),
		]
		order = types.SimpleNamespace(name="POS-1", fiscal_mode="Fiscal")

		receipts, complete = _route_receipt_state(
			order,
			[_route("FISCAL", "DESK-A", "SINV-A"), _route("FISCAL", "DESK-B", "SINV-B")],
		)

		self.assertEqual(receipts, ["RECEIPT-A", "RECEIPT-B"])
		self.assertFalse(complete)

	@patch("erpnext_ua.ua_pos.api._route_receipt_state")
	def test_completed_order_persists_all_receipt_names(self, receipt_state):
		receipt_state.return_value = (["RECEIPT-A", "RECEIPT-B"], True)
		order = types.SimpleNamespace()

		complete = _apply_route_receipt_state(order, [])

		self.assertTrue(complete)
		self.assertEqual(order.prro_receipt, "RECEIPT-A")
		self.assertEqual(json.loads(order.prro_receipts_json), ["RECEIPT-A", "RECEIPT-B"])
		self.assertEqual(order.status, "Completed")
		self.assertIsNone(order.recovery_note)


def _route(fiscal_route: str, cash_desk: str, invoice: str):
	return types.SimpleNamespace(
		route=types.SimpleNamespace(fiscal_route=fiscal_route),
		cash_desk=cash_desk,
		invoice=types.SimpleNamespace(name=invoice),
	)


if __name__ == "__main__":
	unittest.main()
