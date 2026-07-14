import unittest

import frappe

from erpnext_ua.ua_fiscal.payment import canonical_payform_name, fiscal_payform_name
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


if __name__ == "__main__":
	unittest.main()
