import unittest
from datetime import datetime
from unittest.mock import patch

import frappe

from erpnext_ua.ua_pos.doctype.pos_printer.pos_printer import is_lan_address
from erpnext_ua.ua_pos.print_service import (
	EscPosReceipt,
	_qr_svg_data_uri,
	fiscal_snapshot,
	render_browser_fiscal_receipt,
	render_fiscal_report,
	render_order_receipt,
)


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

	def test_browser_qr_is_generated_locally_as_svg(self):
		uri = _qr_svg_data_uri("https://cabinet.tax.gov.ua/cashregs/check?id=1")
		self.assertTrue(uri.startswith("data:image/svg+xml;base64,"))
		self.assertGreater(len(uri), 500)

	def test_render_fiscal_receipt_uses_immutable_xml(self):
		xml = (
			'<?xml version="1.0" encoding="windows-1251"?>'
			"<CHECK><CHECKHEAD><TIN>3184710691</TIN><ORGNM>ФОП Тест</ORGNM>"
			"<POINTNM>Магазин</POINTNM><POINTADDR>м. Рівне</POINTADDR><CASHIER>Касир</CASHIER>"
			"<ORDERDATE>14072026</ORDERDATE><ORDERTIME>131500</ORDERTIME>"
			"<CASHREGISTERNUM>4000545102</CASHREGISTERNUM></CHECKHEAD>"
			"<CHECKTOTAL><SUM>450.00</SUM></CHECKTOTAL>"
			"<CHECKPAY><ROW><PAYFORMCD>0</PAYFORMCD><PAYFORMNM>Cash</PAYFORMNM>"
			"<SUM>450.00</SUM><PROVIDED>500.00</PROVIDED><REMAINS>50.00</REMAINS></ROW></CHECKPAY>"
			"<CHECKBODY><ROW><NAME>Ніж</NAME><AMOUNT>1</AMOUNT><PRICE>450.00</PRICE>"
			"<COST>450.00</COST><TOBACCOQT>20</TOBACCOQT><TOBACCOWEIGHT>0.8</TOBACCOWEIGHT>"
			"<ALCOVOL>0.7</ALCOVOL><ALCOSTRENGTH>40</ALCOSTRENGTH></ROW></CHECKBODY></CHECK>"
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
		self.assertIn("ІД 3184710691".encode("cp1251"), payload)
		self.assertIn("ЧЕК № 6000000001".encode("cp1251"), payload)
		self.assertIn("14.07.2026 13:15:00".encode("cp1251"), payload)
		self.assertIn("ОНЛАЙН".encode("cp1251"), payload)
		self.assertIn("ФН ПРРО 4000545102".encode("cp1251"), payload)
		self.assertIn("ГОТІВКА".encode("cp1251"), payload)
		self.assertIn("ОТРИМАНО".encode("cp1251"), payload)
		self.assertIn("Виробник ПРРО: HUNTER.rv".encode("cp1251"), payload)
		self.assertNotIn(b"Cash", payload)

		snapshot = fiscal_snapshot(receipt, include_qr_image=True)
		self.assertEqual(snapshot["tax_prefix"], "ІД")
		self.assertEqual(snapshot["payments"][0]["means"], "ГОТІВКА")
		self.assertEqual(
			snapshot["qr_data"],
			"https://cabinet.tax.gov.ua/cashregs/check?date=20260714&time=131500&id=6000000001&sm=450.00&fn=4000545102",
		)
		html = render_browser_fiscal_receipt(snapshot, lookup_token="return-token")
		self.assertIn("ГОТІВКА", html)
		self.assertNotIn("Cash", html)
		self.assertIn("ЧЕК № 6000000001", html)
		self.assertIn("ФН ПРРО 4000545102", html)
		self.assertIn("return-token", html)
		self.assertIn("data:image/svg+xml;base64,", html)
		self.assertIn("ОТРИМАНО: 500.00 UAH", html)
		self.assertIn("РЕШТА: 50.00 UAH", html)
		self.assertIn("Кількість тютюнових виробів в одиниці: 20", html)
		self.assertIn("Об’єм алкогольного напою: 0.7 л", html)
		self.assertIn("Виробник ПРРО: HUNTER.rv", html)

	def test_return_uses_fkc2_without_sale_only_totals(self):
		xml = (
			'<?xml version="1.0" encoding="windows-1251"?>'
			"<CHECK><CHECKHEAD><TIN>3184710691</TIN><ORGNM>ФОП Тест</ORGNM>"
			"<POINTNM>Магазин</POINTNM><POINTADDR>м. Рівне</POINTADDR><CASHIER>Касир</CASHIER>"
			"<DOCSUBTYPE>1</DOCSUBTYPE><ORDERDATE>14072026</ORDERDATE><ORDERTIME>141500</ORDERTIME>"
			"<CASHREGISTERNUM>4000545102</CASHREGISTERNUM></CHECKHEAD>"
			"<CHECKTOTAL><SUM>149.00</SUM><RNDSUM>0.05</RNDSUM></CHECKTOTAL>"
			"<CHECKPAY><ROW><PAYFORMCD>0</PAYFORMCD><PAYFORMNM>Cash</PAYFORMNM>"
			"<SUM>149.00</SUM><PROVIDED>200.00</PROVIDED><REMAINS>51.00</REMAINS></ROW></CHECKPAY>"
			"<CHECKBODY><ROW><NAME>Повернений товар</NAME><AMOUNT>1</AMOUNT><PRICE>149.00</PRICE>"
			"<COST>149.00</COST></ROW></CHECKBODY></CHECK>"
		)
		receipt = frappe._dict(
			{
				"name": "PRRO-RETURN-1",
				"status": "Fiscalized",
				"fiscal_number": "6000000002",
				"local_number": 3,
				"is_offline": 0,
				"receipt_xml": xml,
				"total_amount": 149,
			}
		)
		order = frappe._dict(
			{
				"name": "POS-RETURN-1",
				"fiscal_mode": "Fiscal",
				"prro_receipt": receipt.name,
				"lookup_token": "return-token",
			}
		)
		printer = frappe._dict({"characters_per_line": 48, "encoding": "cp1251", "code_page": 46})
		snapshot = fiscal_snapshot(receipt)
		html = render_browser_fiscal_receipt(snapshot)
		self.assertIn("<b>СУМА</b>", html)
		self.assertIn("ВИДАТКОВИЙ ЧЕК", html)
		self.assertNotIn("ДО СПЛАТИ", html)
		self.assertNotIn("ОТРИМАНО", html)
		self.assertNotIn("РЕШТА", html)
		self.assertNotIn("Заокруглення", html)

		with patch("frappe.get_doc", return_value=receipt):
			payload = render_order_receipt(order, printer)
		self.assertIn("СУМА".encode("cp1251"), payload)
		self.assertIn("ВИДАТКОВИЙ ЧЕК".encode("cp1251"), payload)
		self.assertNotIn("ДО СПЛАТИ".encode("cp1251"), payload)
		self.assertNotIn("ОТРИМАНО".encode("cp1251"), payload)
		self.assertNotIn("РЕШТА".encode("cp1251"), payload)

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
		self.assertIn("Розрахунковий залишок".encode("cp1251"), payload)

	def test_render_z_report_labels_fiscal_fields_unambiguously(self):
		printer = frappe._dict({"characters_per_line": 48, "encoding": "cp1251", "code_page": 46})
		report = {
			"report_type": "Z",
			"title": "Z-ЗВІТ",
			"organization": "КОЗЯРЧУК РОМАН",
			"tax_prefix": "ІД",
			"tax_number": "3423612974",
			"cash_register_fiscal_number": "4000545102",
			"cash_desk_local_number": 4,
			"shift": "SHIFT-1",
			"cashier": "Касир",
			"opened_at": "14.07.2026 20:37:41",
			"closed_at": "14.07.2026 21:07:59",
			"document_at": "14.07.2026 21:07:57",
			"fiscal_number": "7324103331",
			"fiscal_number_label": "Фіскальний № Z-звіту",
			"local_number": 3199,
			"generated_at": "14.07.2026 21:44:55",
		}
		payload = render_fiscal_report(report, printer)
		self.assertIn("ІД 3423612974".encode("cp1251"), payload)
		self.assertIn("ФН ПРРО 4000545102".encode("cp1251"), payload)
		self.assertIn("Фіскальний № Z-звіту 7324103331".encode("cp1251"), payload)
		self.assertIn("Z-документ: 14.07.2026 21:07:57".encode("cp1251"), payload)
		self.assertIn("Локальний № документа 3199".encode("cp1251"), payload)
		self.assertIn("Надруковано: 14.07.2026 21:44:55".encode("cp1251"), payload)
		self.assertNotIn(b".998758", payload)


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
