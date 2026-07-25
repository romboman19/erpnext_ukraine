import unittest

import frappe

from erpnext_ua.ua_fiscal.payment import canonical_payform_name, fiscal_payform_name, normalize_payment_method
from erpnext_ua.ua_pos.api import _fiscal_head_datetime, _z_report_totals


class TestFiscalReportData(unittest.TestCase):
	def test_legacy_english_payment_is_localized_from_immutable_z_xml(self):
		receipt = frappe._dict(
			{
				"receipt_xml": (
					'<?xml version="1.0" encoding="windows-1251"?>'
					"<ZREP><ZREPHEAD><ORDERDATE>14072026</ORDERDATE><ORDERTIME>222325</ORDERTIME>"
					"</ZREPHEAD><ZREPREALIZ><SUM>149.00</SUM><ORDERSCNT>1</ORDERSCNT>"
					"<PAYFORMS><ROW><PAYFORMCD>0</PAYFORMCD><PAYFORMNM>Cash</PAYFORMNM>"
					"<SUM>149.00</SUM></ROW></PAYFORMS></ZREPREALIZ>"
					"<ZREPBODY><SERVICEINPUT>10.00</SERVICEINPUT></ZREPBODY></ZREP>"
				)
			}
		)

		totals = _z_report_totals(receipt)

		self.assertEqual(totals["realiz"]["sum"], 149)
		self.assertEqual(totals["realiz"]["count"], 1)
		self.assertEqual(totals["realiz"]["payforms"][0]["name"], "ГОТІВКА")
		self.assertEqual(totals["service_input"], 10)

	def test_fiscal_document_timestamp_comes_from_xml_head(self):
		self.assertEqual(
			_fiscal_head_datetime({"ORDERDATE": "14072026", "ORDERTIME": "222325"}),
			"14.07.2026 22:23:25",
		)

	def test_fiscal_payment_names_are_ukrainian(self):
		self.assertEqual(canonical_payform_name(0, "Cash"), "ГОТІВКА")
		self.assertEqual(canonical_payform_name(1, "Credit Card"), "КАРТКА")
		self.assertEqual(fiscal_payform_name("IBAN", 2, "Bank Transfer"), "ПЕРЕКАЗ НА РАХУНОК")
		self.assertEqual(canonical_payform_name(100000, "LiqPay"), "LiqPay")

	def test_payment_method_configuration_is_authoritative(self):
		method = normalize_payment_method(
			{
				"name": "LiqPay",
				"enabled": 1,
				"ua_pos_enabled": 1,
				"ua_pos_channel": "Інтернет-еквайринг",
				"ua_prro_payment_form": "БЕЗГОТІВКОВА",
				"ua_prro_payment_means": "LiqPay",
				"ua_payformcd": 100000,
				"ua_allow_cashless": 1,
				"ua_allow_other": 0,
				"ua_allow_prepayment": 1,
				"ua_allow_debt": 1,
				"ua_requires_terminal": 0,
				"ua_prro_code_verified": 1,
				"ua_currency": "UAH",
			}
		)
		self.assertEqual(method["kind"], "IBAN")
		self.assertEqual(method["payment_form"], "БЕЗГОТІВКОВА")
		self.assertEqual(method["payment_means"], "LiqPay")
		self.assertEqual(method["payment_code"], 100000)

	def test_non_cash_method_cannot_use_cash_xml_code(self):
		with self.assertRaisesRegex(ValueError, "не може мати код PAYFORMCD 0"):
			normalize_payment_method(
				{
					"name": "Помилковий запис",
					"enabled": 1,
					"ua_pos_enabled": 1,
					"ua_pos_channel": "Інтернет-еквайринг",
					"ua_prro_payment_form": "БЕЗГОТІВКОВА",
					"ua_prro_payment_means": "Картка",
					"ua_payformcd": 0,
					"ua_allow_cashless": 1,
					"ua_allow_other": 0,
					"ua_allow_prepayment": 0,
					"ua_allow_debt": 0,
					"ua_requires_terminal": 0,
					"ua_prro_code_verified": 1,
					"ua_currency": "UAH",
				}
			)


if __name__ == "__main__":
	unittest.main()
