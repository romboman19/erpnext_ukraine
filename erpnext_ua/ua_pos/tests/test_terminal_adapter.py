import unittest
from unittest.mock import patch

from erpnext_ua.ua_pos import terminal_service
from erpnext_ua.ua_pos.adapters.terminal import PrivatPosAdapter, PrivatPOSGatewayClient


class FakeClient:
	def __init__(self, result):
		self.result = result
		self.calls = []

	def operation(self, *args, **kwargs):
		self.calls.append((args, kwargs))
		return self.result

	def status(self, *args, **kwargs):
		self.calls.append((args, kwargs))
		return self.result

	def ping(self, *args, **kwargs):
		return self.result


class RecordingGateway(PrivatPOSGatewayClient):
	def __init__(self, responses):
		super().__init__("http://gateway.invalid", "test-key")
		self.responses = list(responses)
		self.paths = []

	def _request(self, method, path, *, json=None):
		self.paths.append(path)
		return self.responses.pop(0)


class TestPrivatPosAdapter(unittest.TestCase):
	def test_identify_uses_official_service_message(self):
		gateway = RecordingGateway([{"params": {"vendor": "Ingenico", "model": "DESK3200"}}])
		result = gateway.identify("127.0.0.1", 2001)
		self.assertEqual(result["params"]["model"], "DESK3200")
		self.assertEqual(gateway.paths, ["/identify"])

	def test_terminal_version_maps_profile_and_serial_without_inventing_merchant(self):
		values = terminal_service._technical_terminal_values(
			{"params": {"version": "TA7E161.102 S1RO0L2E00DK19263785"}},
			{"params": {"vendor": "Ingenico", "model": "DESK3200"}},
		)
		self.assertEqual(values["software_version"], "TA7E161.102")
		self.assertEqual(values["terminal_profile_id"], "S1RO0L2E00")
		self.assertEqual(values["terminal_serial_number"], "DK19263785")
		self.assertEqual(values["device_id"], "DK19263785")
		self.assertEqual(values["terminal_vendor"], "Ingenico")
		self.assertEqual(values["terminal_model"], "DESK3200")
		self.assertNotIn("merchant_id", values)

	def test_terminal_info_uses_read_only_gateway_endpoint(self):
		gateway = RecordingGateway([{"params": {"MerchantId": "M-1"}}])
		result = gateway.terminal_info("127.0.0.1", 2001)
		self.assertEqual(result["params"]["MerchantId"], "M-1")
		self.assertEqual(gateway.paths, ["/terminalinfo"])

	def test_terminal_info_maps_firmware_aliases_to_fiscal_fields(self):
		values = terminal_service._terminal_info_values(
			{
				"params": {
					"MerchantId": "M-1",
					"TerminalID": "T-1",
					"paymentSystem": {"NAME": "VISA", "TAXNUM": "100"},
					"ACQUIRENM": "АТ КБ Банк",
					"ACQUIREPN": "200",
				}
			}
		)
		self.assertEqual(
			values,
			{
				"merchant_id": "M-1",
				"device_id": "T-1",
				"payment_system_name": "VISA",
				"payment_system_tax_number": "100",
				"acquirer_name": "АТ КБ Банк",
				"acquirer_tax_number": "200",
			},
		)

	def test_service_maps_gateway_url_to_client_base_url(self):
		with patch.object(
			terminal_service,
			"_settings",
			return_value={
				"gateway_url": "http://gateway.invalid/",
				"api_key": "test-key",
				"timeout": 17,
			},
		):
			adapter = terminal_service.get_adapter()

		self.assertEqual(adapter.client.base_url, "http://gateway.invalid")
		self.assertEqual(adapter.client.api_key, "test-key")
		self.assertEqual(adapter.client.timeout, 17)

	def test_sale_maps_approved_response(self):
		client = FakeClient({"responseCode": "0000", "rrn": "42", "invoiceNumber": "7"})
		result = PrivatPosAdapter(client).sale({"ip": "127.0.0.1", "port": 2000}, 10, "op-1")
		self.assertEqual(result.status, "confirmed")
		self.assertEqual(result.rrn, "42")
		self.assertEqual(len(client.calls), 1)

	def test_sale_maps_nested_fiscal_details_from_official_json_response(self):
		client = FakeClient(
			{
				"method": "Purchase",
				"params": {
					"responseCode": "0000",
					"rrn": "42",
					"invoiceNumber": "7",
					"approvalCode": "A1",
					"pan": "4111XXXXXXXX1111",
					"merchant": "MERCHANT-1",
					"terminalId": "TERM-1",
					"paymentSystem": "VISA",
					"bankAcquirer": "PrivatBank",
				},
				"error": False,
			}
		)
		result = PrivatPosAdapter(client).sale({"ip": "127.0.0.1", "port": 2000}, 10, "op-1b")
		self.assertEqual(result.status, "confirmed")
		self.assertEqual(result.rrn, "42")
		self.assertEqual(result.auth_code, "A1")
		self.assertEqual(result.merchant_id, "MERCHANT-1")
		self.assertEqual(result.device_id, "TERM-1")
		self.assertEqual(result.payment_system_name, "VISA")
		self.assertEqual(result.acquirer_name, "PrivatBank")

	def test_timeout_is_unknown_and_not_retried(self):
		client = FakeClient({"error": True, "description": "timeout"})
		result = PrivatPosAdapter(client).sale({"ip": "127.0.0.1", "port": 2000}, 10, "op-2")
		self.assertEqual(result.status, "unknown")
		self.assertEqual(len(client.calls), 1)

	def test_status_uses_original_operation_id(self):
		client = FakeClient({"status": "approved"})
		result = PrivatPosAdapter(client).status({"ip": "127.0.0.1", "port": 2000}, "op-3")
		self.assertEqual(result.status, "confirmed")
		self.assertEqual(client.calls[0][0][1], "op-3")

	def test_server_error_is_unknown_not_a_second_payment(self):
		gateway = RecordingGateway([{"error": True, "_http_status": 500, "description": "gateway error"}])
		result = gateway.operation("sale", "127.0.0.1", 10, "op-4")
		self.assertTrue(result["error"])
		self.assertEqual(gateway.paths, ["/v1/pos/operation"])

	def test_legacy_fallback_is_used_only_for_definite_404(self):
		gateway = RecordingGateway(
			[
				{"error": True, "_http_status": 404},
				{"responseCode": "0000", "_http_status": 200},
			]
		)
		result = gateway.operation("sale", "127.0.0.1", 10, "op-5")
		self.assertEqual(result["responseCode"], "0000")
		self.assertEqual(gateway.paths, ["/v1/pos/operation", "/purchase"])


if __name__ == "__main__":
	unittest.main()
