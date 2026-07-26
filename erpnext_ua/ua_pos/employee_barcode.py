"""Sequential EAN-13 barcodes for UA POS employees."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.naming import make_autoname


EAN13_PREFIX = "9910"
EAN13_PAYLOAD_LENGTH = 12
SEQUENCE_DIGITS = EAN13_PAYLOAD_LENGTH - len(EAN13_PREFIX)
NAMING_SERIES = f"{EAN13_PREFIX}.{SEQUENCE_DIGITS * '#'}"
MAX_GENERATION_ATTEMPTS = 100


def assign_employee_barcode(doc, method: str | None = None) -> None:
	"""Assign a barcode before an Employee is validated and preserve it afterwards."""
	barcode = str(doc.get("ua_pos_barcode") or "").strip()
	if barcode:
		if not is_valid_employee_barcode(barcode):
			frappe.throw(_("Штрихкод касира має бути валідним EAN-13 з префіксом 9910"))
		doc.ua_pos_barcode = barcode
		return

	doc.ua_pos_barcode = generate_employee_barcode()


def backfill_employee_barcodes() -> int:
	"""Assign barcodes to existing Employees in creation order."""
	Employee = frappe.qb.DocType("Employee")
	rows = (
		frappe.qb.from_(Employee)
		.select(Employee.name)
		.where((Employee.ua_pos_barcode.isnull()) | (Employee.ua_pos_barcode == ""))
		.orderby(Employee.creation)
	).run()

	for (employee_name,) in rows:
		frappe.db.set_value(
			"Employee",
			employee_name,
			"ua_pos_barcode",
			generate_employee_barcode(),
			update_modified=False,
		)
	return len(rows)


def generate_employee_barcode() -> str:
	"""Generate the next unused barcode using Frappe's locked naming series."""
	for _attempt in range(MAX_GENERATION_ATTEMPTS):
		payload = make_autoname(NAMING_SERIES)
		if len(payload) != EAN13_PAYLOAD_LENGTH:
			frappe.throw(_("Діапазон штрихкодів касира з префіксом 9910 вичерпано"))
		barcode = payload + ean13_check_digit(payload)
		if not _barcode_exists(barcode):
			return barcode

	frappe.throw(_("Не вдалося згенерувати унікальний штрихкод касира"))


def is_valid_employee_barcode(value: str | None) -> bool:
	barcode = str(value or "").strip()
	if len(barcode) != 13 or not barcode.isdigit() or not barcode.startswith(EAN13_PREFIX):
		return False
	return barcode[-1] == ean13_check_digit(barcode[:EAN13_PAYLOAD_LENGTH])


def ean13_check_digit(payload: str) -> str:
	"""Return the EAN-13 check digit for a 12-digit payload."""
	if len(payload) != EAN13_PAYLOAD_LENGTH or not payload.isdigit():
		raise ValueError("EAN-13 payload must contain exactly 12 digits")
	weighted_sum = sum(
		int(digit) * (1 if index % 2 == 0 else 3)
		for index, digit in enumerate(payload)
	)
	return str((-weighted_sum) % 10)


def _barcode_exists(barcode: str) -> bool:
	return bool(frappe.db.exists("Employee", {"ua_pos_barcode": barcode}))
