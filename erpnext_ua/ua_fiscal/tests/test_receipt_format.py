import unittest
from datetime import datetime

from erpnext_ua.ua_fiscal.receipt_format import build_verification_url, offline_control_number


class TestReceiptFormat(unittest.TestCase):
	def test_online_verification_url_contains_seconds(self):
		self.assertEqual(
			build_verification_url("4000545102", "7323943574", 149, datetime(2026, 7, 14, 20, 37, 41)),
			"https://cabinet.tax.gov.ua/cashregs/check?date=20260714&time=203741&id=7323943574&sm=149.00&fn=4000545102",
		)

	def test_offline_verification_url_has_mac_and_control_number(self):
		url = build_verification_url(
			"4000545102",
			"52330658.7.9675",
			149,
			datetime(2026, 7, 14, 20, 37, 41),
			mac="A1B2C3",
		)
		self.assertIn("?mac=A1B2C3&date=20260714&time=203741", url)
		self.assertEqual(offline_control_number("52330658.7.9675"), "9675")


if __name__ == "__main__":
	unittest.main()
