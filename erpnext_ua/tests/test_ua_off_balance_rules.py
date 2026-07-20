import unittest

from erpnext_ua.ua_accounting.off_balance_rules import (
	build_source_key,
	direction_sign,
	signed_values,
	validate_available_balance,
	validate_magnitudes,
)


class TestUAOffBalanceRules(unittest.TestCase):
	def test_direction_and_signed_values(self):
		self.assertEqual(direction_sign("Increase"), 1)
		self.assertEqual(direction_sign("Decrease"), -1)
		self.assertEqual(signed_values("Increase", "2.5", "100.25"), (2.5, 100.25))
		self.assertEqual(signed_values("Decrease", "2.5", "100.25"), (-2.5, -100.25))

	def test_magnitudes_reject_ambiguous_or_negative_values(self):
		for quantity, amount in ((0, 0), (-1, 0), (0, -1), ("NaN", 1)):
			with self.subTest(quantity=quantity, amount=amount), self.assertRaises(ValueError):
				validate_magnitudes(quantity, amount)

	def test_source_key_is_stable_and_scoped(self):
		values = {
			"company": "Demo UA",
			"account": "024 - Demo UA",
			"direction": "Increase",
			"reference_doctype": "Purchase Receipt",
			"reference_name": "MAT-PRE-0001",
			"reference_detail": "row-1",
		}
		self.assertEqual(build_source_key(**values), build_source_key(**values))
		self.assertNotEqual(build_source_key(**values), build_source_key(**{**values, "direction": "Decrease"}))
		self.assertIsNone(
			build_source_key(company="Demo UA", account="024 - Demo UA", direction="Increase")
		)

	def test_decrease_cannot_exceed_dimension_balance(self):
		validate_available_balance(
			available_quantity=2,
			available_amount=100,
			requested_quantity=2,
			requested_amount=100,
		)
		with self.assertRaises(ValueError):
			validate_available_balance(
				available_quantity=2,
				available_amount=100,
				requested_quantity=3,
				requested_amount=100,
			)
		with self.assertRaises(ValueError):
			validate_available_balance(
				available_quantity=2,
				available_amount=100,
				requested_quantity=2,
				requested_amount=101,
			)


if __name__ == "__main__":
	unittest.main()
