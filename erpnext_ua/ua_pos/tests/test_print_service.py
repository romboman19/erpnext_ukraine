import unittest
from datetime import datetime
from unittest.mock import patch

import frappe

from erpnext_ua.ua_pos.doctype.pos_printer.pos_printer import is_lan_address
from erpnext_ua.ua_pos.print_service import EscPosReceipt, render_fiscal_report, render_order_receipt


class TestPrintService(unittest.TestCase):
	def test_lan_address_allowlist(self):
		self.assertTrue(is_lan_address("10.20.30.40"))
		self.assertTrue(is_lan_address("172.16.1.2"))
		self.assertTrue(is_lan_address("192.168.1.100"))
		self.assertTrue(is_lan_address("fd00::1"))
		self.assertFalse(is_lan_address("127.0.0.1"))
		self.assertFalse(is_lan_address("169.254.1.1"))
		self.assertFalse(is_lan_address("8.8.8.8"))

	def test_escpos_payload_has_cyrillic_qr_and_cut(self):
		builder = EscPosReceipt(width=32, encoding="cp1251", code_page=46)
		builder.text("Фіскальний чек", align="center", bold=True)
		builder.qr("https://example.invalid/check/1")
		payload = builder.finish()
		self.assertIn("Фіскальний чек".encode("cp1251"), payload)
		self.assertIn(b"1P0https://example.invalid/check/1", payload)
		self.assertTrue(payload.endswith(b"\x1dV\x00"))

	def test_render_fiscal_receipt_uses_immutable_xml(self):
		xml = (
			'<?xml version="1.0" encoding="windows-1251"?>'
			"<CHECK><CHECKHEAD><TIN>3184710691</TIN><ORGNM>ФОП Тест</ORGNM>"
			"<POINTNM>Магазин</POINTNM><POINTADDR>м. Рівне</POINTADDR><CASHIER>Касир</CASHIER>"
			"<ORDERDATE>14072026</ORDERDATE><ORDERTIME>131500</ORDERTIME></CHECKHEAD>"
			"<CHECKTOTAL><SUM>450.00</SUM></CHECKTOTAL>"
			"<CHECKPAY><ROW><PAYFORMNM>ГОТІВКА</PAYFORMNM><SUM>450.00</SUM></ROW></CHECKPAY>"
			"<CHECKBODY><ROW><NAME>Ніж</NAME><AMOUNT>1</AMOUNT><PRICE>450.00</PRICE>"
			"<COST>450.00</COST></ROW></CHECKBODY></CHECK>"
		)
		receipt = frappe._dict(
			{
				"name": "PRRO-1",
				"status": "Fiscalized",
				"fiscal_number": "6000000001",
				"local_number": 2,
				"is_offline": 0,
				"receipt_xml": xml,
				"total_amount": 450,
				"qr_data": "https://example.invalid/fiscal/6000000001",
			}
		)
		order = frappe._dict(
			{
				"name": "POS-ORDER-1",
				"fiscal_mode": "Fiscal",
				"prro_receipt": receipt.name,
				"change_amount": 0,
				"lookup_token": "return-token",
			}
		)
		printer = frappe._dict({"characters_per_line": 48, "encoding": "cp1251", "code_page": 46})
		with patch("frappe.get_doc", return_value=receipt), patch(
			"frappe.utils.now_datetime", return_value=datetime(2026, 7, 14, 13, 15)
		):
			payload = render_order_receipt(order, printer, is_copy=True)
		self.assertIn("ФОП Тест".encode("cp1251"), payload)
		self.assertIn(b"6000000001", payload)
		self.assertIn("КОПІЯ".encode("cp1251"), payload)

	def test_render_x_report_contains_shift_totals(self):
		printer = frappe._dict({"characters_per_line": 48, "encoding": "cp1251", "code_page": 46})
		report = {
			"report_type": "X",
			"title": "X-ЗВІТ",
			"non_fiscal": True,
			"testing": True,
			"organization": "КОЗЯРЧУК РОМАН",
			"tax_id": "3423612974",
			"point_name": "Магазин Hunter",
			"cash_register_fiscal_number": "4000545102",
			"cash_desk_local_number": 4,
			"shift": "SHIFT-1",
			"cashier": "Касир",
			"opened_at": "2026-07-14 20:37:41",
			"receipts_count": 2,
			"sales_total": 298,
			"returns_total": 0,
			"net_total": 298,
			"service_input": 100,
			"service_output": 0,
			"cash_balance": 398,
			"sales_payforms": [{"code": 0, "name": "ГОТІВКА", "sum": 298}],
			"generated_at": "2026-07-14 21:00:00",
		}
		payload = render_fiscal_report(report, printer)
		self.assertIn("X-ЗВІТ".encode("cp1251"), payload)
		self.assertIn("НЕФІСКАЛЬНИЙ".encode("cp1251"), payload)
		self.assertIn("298.00 грн".encode("cp1251"), payload)
		self.assertIn("Готівка в касі".encode("cp1251"), payload)


if __name__ == "__main__":
	unittest.main()


def run_queue_flow():
	"""Site integration smoke test for immutable print jobs and worker state transitions."""
	from erpnext_ua.ua_pos import print_service

	printer_name = "_pos_printer_selftest"
	frappe.db.delete("POS Print Job", {"printer": printer_name})
	frappe.db.delete("POS Printer", {"name": printer_name})
	printer = frappe.get_doc(
		{
			"doctype": "POS Printer",
			"printer_name": printer_name,
			"host": "192.168.254.254",
			"port": 9100,
		}
	).insert(ignore_permissions=True)
	company = frappe.get_all("Company", pluck="name", limit=1)[0]
	job = frappe.get_doc(
		{
			"doctype": "POS Print Job",
			"printer": printer.name,
			"cash_desk": frappe.get_all("POS Cash Desk", pluck="name", limit=1)[0]
			if frappe.db.count("POS Cash Desk")
			else None,
			"job_type": "Report",
			"reference_doctype": "Company",
			"reference_name": company,
			"format": "Raw",
			"idem_key": "print-selftest",
			"payload_base64": "dGVzdC1wcmludA==",
		}
	)
	# Cash desk is mandatory by design. Use a temporary direct value only when the
	# isolated site has no configured desk; link validation is irrelevant to this smoke test.
	if not job.cash_desk:
		job.cash_desk = "_test_desk"
		job.flags.ignore_links = True
	job.insert(ignore_permissions=True)
	frappe.db.commit()

	class FakeConnection:
		payload = None

		def __enter__(self):
			return self

		def __exit__(self, *_args):
			return False

		def settimeout(self, _timeout):
			pass

		def sendall(self, payload):
			self.payload = payload

	connection = FakeConnection()
	with patch.object(print_service, "_private_endpoint", return_value=("192.168.254.254", 9100)), patch.object(
		print_service, "audit"
	), patch("socket.create_connection", return_value=connection):
		print_service.process_print_job(job.name)

	job.reload()
	assert job.status == "Done" and connection.payload == b"test-print", job.as_dict()
	result = {"job": job.name, "status": job.status, "attempts": job.attempts}
	frappe.db.delete("POS Print Job", {"name": job.name})
	frappe.db.delete("POS Printer", {"name": printer.name})
	frappe.db.commit()
	return result
