import unittest

from erpnext_ua.ua_price_tags.domain import PROMOTIONAL, STANDARD, choose_price, copies_for, job_group_key


class TestPriceTagDomain(unittest.TestCase):
	def test_only_a_lower_positive_price_is_promotional(self):
		self.assertEqual(choose_price(699, 599), (PROMOTIONAL, 599))
		self.assertEqual(choose_price(699, 699), (STANDARD, 699))
		self.assertEqual(choose_price(699, 799), (STANDARD, 699))
		self.assertEqual(choose_price(699, 0), (STANDARD, 699))

	def test_missing_regular_price_is_not_silently_replaced(self):
		self.assertEqual(choose_price(None, 599), (STANDARD, None))
		self.assertEqual(choose_price(0, 599), (STANDARD, None))

	def test_copy_modes_are_positive_and_quantity_rounds_up(self):
		self.assertEqual(copies_for("One", 20, 8), 1)
		self.assertEqual(copies_for("Source Quantity", 2.2, 1), 3)
		self.assertEqual(copies_for("Source Quantity", -4, 1), 4)
		self.assertEqual(copies_for("Manual Copies", 1, 7), 7)
		self.assertEqual(copies_for("Manual Copies", 1, 0), 1)
		self.assertEqual(copies_for("Manual Copies", 1, "invalid"), 1)
		self.assertEqual(copies_for("Source Quantity", 5000, 1), 1000)

	def test_group_key_separates_warehouse_and_template(self):
		self.assertNotEqual(
			job_group_key({"warehouse": "Store A", "template_type": STANDARD}, "40×25 mm"),
			job_group_key({"warehouse": "Store B", "template_type": STANDARD}, "40×25 mm"),
		)
		self.assertNotEqual(
			job_group_key({"warehouse": "Store A", "template_type": STANDARD}, "40×25 mm"),
			job_group_key({"warehouse": "Store A", "template_type": PROMOTIONAL}, "40×25 mm"),
		)


if __name__ == "__main__":
	unittest.main()
