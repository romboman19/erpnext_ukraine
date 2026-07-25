import json
import unittest
from datetime import datetime

from erpnext_ua.ua_fiscal.fiscal_client import FiscalClient, FiscalServerError


class _Response:
	status_code = 200
	content = b'{"UID":"ok"}'
	text = content.decode()

	def json(self):
		return json.loads(self.content)


class _HTTP:
	def __init__(self):
		self.headers = {}
		self.last = None

	def post(self, url, **kwargs):
		self.last = {"url": url, **kwargs}
		return _Response()


class _Settings:
	request_timeout = 15

	@staticmethod
	def get_fiscal_server_url():
		return "https://fs.example.test/fs"


class TestFiscalCommand(unittest.TestCase):
	def test_command_timestamp_has_offset_and_timeout_is_milliseconds(self):
		http = _HTTP()
		client = FiscalClient(settings=_Settings(), http=http)
		client.server_state()
		body = json.loads(http.last["data"])
		stamp = datetime.fromisoformat(body["Timestamp"])
		self.assertIsNotNone(stamp.utcoffset())
		self.assertEqual(body["Timeout"], 15_000)
		self.assertEqual(body["Command"], "ServerState")

	def test_json_error_preserves_dps_error_code(self):
		http = _HTTP()
		http.post = lambda *args, **kwargs: type(
			"ErrorResponse",
			(),
			{
				"status_code": 200,
				"content": b'{"ErrorCode":9,"ErrorMessage":"validation"}',
				"text": '{"ErrorCode":9,"ErrorMessage":"validation"}',
				"json": lambda self: json.loads(self.content),
			},
		)()
		client = FiscalClient(settings=_Settings(), http=http)
		with self.assertRaises(FiscalServerError) as caught:
			client.server_state()
		self.assertEqual(caught.exception.error_code, 9)

	def test_document_info_returns_none_when_dps_reports_absent_data(self):
		http = _HTTP()
		http.post = lambda *args, **kwargs: type(
			"AbsentResponse",
			(),
			{
				"status_code": 200,
				"content": b'{"ErrorCode":13,"ErrorMessage":"requested data is absent"}',
				"text": '{"ErrorCode":13,"ErrorMessage":"requested data is absent"}',
				"json": lambda self: json.loads(self.content),
			},
		)()
		client = FiscalClient(settings=_Settings(), http=http)
		client.sign = lambda data, kep_key, *, online=True: data
		self.assertIsNone(client.document_info_by_local_number("4000545102", 3198, "test-key"))


if __name__ == "__main__":
	unittest.main()
