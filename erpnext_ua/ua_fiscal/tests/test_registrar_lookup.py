import unittest

from erpnext_ua.ua_fiscal.registrar_lookup import parse_objects


class TestRegistrarLookup(unittest.TestCase):
	def test_flattens_units_and_registrars_into_one_row_each(self):
		payload = {
			"UID": "req-1",
			"TaxObjects": [
				{
					"Name": "Магазин на Соборній",
					"Address": "м. Рівне, вул. Соборна, 1",
					"TransactionsRegistrars": [
						{"NumFiscal": 4000545102, "NumLocal": 1, "Name": "Каса 1", "Closed": False},
						{"NumFiscal": 4000545103, "NumLocal": 2, "Name": "Каса 2", "Closed": False},
					],
				},
				{
					"Name": "Інтернет-магазин",
					"Address": "м. Рівне, вул. Соборна, 1",
					"TransactionsRegistrars": [
						{"NumFiscal": 4000545104, "NumLocal": 1, "Name": "Веб-каса", "Closed": False},
					],
				},
			],
		}

		rows = parse_objects(payload)

		self.assertEqual(len(rows), 3)
		self.assertEqual(rows[0]["unit_name"], "Магазин на Соборній")
		self.assertEqual(rows[0]["fiscal_number"], "4000545102")
		self.assertEqual(rows[0]["register_local_number"], 1)
		self.assertEqual(rows[2]["unit_name"], "Інтернет-магазин")
		self.assertEqual(rows[2]["register_name"], "Веб-каса")

	def test_skips_closed_registrars(self):
		payload = {
			"TaxObjects": [
				{
					"Name": "Магазин",
					"Address": "адреса",
					"TransactionsRegistrars": [
						{"NumFiscal": 1, "NumLocal": 1, "Name": "Стара каса", "Closed": True},
						{"NumFiscal": 2, "NumLocal": 2, "Name": "Діюча каса", "Closed": False},
					],
				}
			]
		}

		rows = parse_objects(payload)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["register_name"], "Діюча каса")

	def test_empty_payload_returns_no_rows(self):
		self.assertEqual(parse_objects(None), [])
		self.assertEqual(parse_objects({}), [])
		self.assertEqual(parse_objects({"TaxObjects": []}), [])


if __name__ == "__main__":
	unittest.main()
