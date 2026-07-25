from frappe.tests import IntegrationTestCase

from erpnext_ua.ua_fop.taxpayer_cabinet import _valid_signer_tax_ids, parse_taxpayer_card


class TestTaxpayerCabinetParsing(IntegrationTestCase):
	def test_ignores_invalid_signer_tax_id_placeholder(self):
		self.assertEqual(_valid_signer_tax_ids({"ipn": "0", "edrpou": None}), set())
		self.assertEqual(
			_valid_signer_tax_ids({"ipn": "3423612974", "edrpou": "12345678"}),
			{"3423612974", "12345678"},
		)

	def test_maps_documented_payer_card_groups(self):
		payload = [
			{
				"idGroup": 1,
				"headers": {"TIN": "Податковий номер", "FULL_NAME": "Повне найменування"},
				"values": {"TIN": "3184710691", "FULL_NAME": "ФОП ТЕСТОВИЙ ПЛАТНИК"},
			},
			{
				"idGroup": 2,
				"headers": {"ADRESS": "Податкова адреса", "STATE": "Стан"},
				"values": {"ADRESS": "м. Київ, вул. Тестова, 1", "STATE": "на обліку"},
			},
			{
				"idGroup": 5,
				"headers": {"KOD_PDV": "Номер платника ПДВ", "DAT_ANUL": "Дата анулювання"},
				"listValues": [{"KOD_PDV": "318471069112", "DAT_ANUL": None}],
			},
			{
				"idGroup": 6,
				"headers": {"GRUP": "Група", "DATA_N": "Дата переходу на спрощену систему оподаткування"},
				"values": {"GRUP": "3 група", "DATA_N": "01.01.2024"},
			},
			{
				"idGroup": 14,
				"headers": {"CLIENT_COUNT": "IBAN", "MFO_NAME": "Назва банку", "CODE_CURRENCY_NAME": "Валюта"},
				"listValues": [
					{
						"CLIENT_COUNT": "UA033000010000000000000000001",
						"MFO_NAME": "ТЕСТ БАНК",
						"CODE_CURRENCY_NAME": "980 УКРАЇНСЬКА ГРИВНЯ",
					}
				],
			},
			{
				"idGroup": 16,
				"headers": {"KVED": "Код ВЕД", "KVED_NAME": "Найменування ВЕД", "IS_MAIN": "Основна"},
				"listValues": [
					{"KVED": "_47.91", "KVED_NAME": "Роздрібна торгівля", "IS_MAIN": 1},
					{"KVED": "_62.01", "KVED_NAME": "Комп'ютерне програмування", "IS_MAIN": 0},
				],
			},
		]
		result = parse_taxpayer_card(payload, expected_tax_id="3184710691")
		self.assertEqual(result["updates"]["fop_full_name"], "ФОП ТЕСТОВИЙ ПЛАТНИК")
		self.assertEqual(result["updates"]["single_tax_group"], "3")
		self.assertEqual(result["updates"]["tax_rate_mode"], "3% з ПДВ")
		self.assertEqual(result["updates"]["single_tax_registration_date"], "2024-01-01")
		self.assertEqual(result["updates"]["iban"], "UA033000010000000000000000001")
		self.assertEqual(result["updates"]["kved_main"], "47.91")
		self.assertEqual(len(result["kveds"]), 2)

	def test_rejects_data_for_another_taxpayer(self):
		payload = [
			{
				"idGroup": 1,
				"headers": {"TIN": "Податковий номер", "FULL_NAME": "Повне найменування"},
				"values": {"TIN": "1234567890", "FULL_NAME": "OTHER"},
			}
		]
		with self.assertRaisesRegex(ValueError, "another taxpayer"):
			parse_taxpayer_card(payload, expected_tax_id="3184710691")
