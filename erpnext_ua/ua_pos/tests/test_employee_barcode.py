from __future__ import annotations

import unittest
from unittest.mock import patch

from erpnext_ua.ua_pos.employee_barcode import (
	NAMING_SERIES,
	ean13_check_digit,
	generate_employee_barcode,
	is_valid_employee_barcode,
)


class TestEmployeeBarcode(unittest.TestCase):
	def test_ean13_check_digit_matches_reference_value(self):
		self.assertEqual(ean13_check_digit("400638133393"), "1")

	def test_internal_employee_barcode_uses_9910_prefix(self):
		self.assertEqual(NAMING_SERIES, "9910.########")
		self.assertTrue(is_valid_employee_barcode("9910000000010"))
		self.assertFalse(is_valid_employee_barcode("9910000000011"))
		self.assertFalse(is_valid_employee_barcode("4820000000015"))

	@patch("erpnext_ua.ua_pos.employee_barcode._barcode_exists", side_effect=[True, False])
	@patch(
		"erpnext_ua.ua_pos.employee_barcode.make_autoname",
		side_effect=["991000000001", "991000000002"],
	)
	def test_generator_skips_an_existing_barcode(self, _make_autoname, _exists):
		self.assertEqual(generate_employee_barcode(), "9910000000027")


if __name__ == "__main__":
	unittest.main()
