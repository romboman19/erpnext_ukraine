from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, nowdate

from erpnext_ua.ua_accounting.off_balance_rules import (
	build_source_key,
	validate_available_balance,
	validate_magnitudes,
)


ENTRY_FIELDS = (
	"company",
	"posting_date",
	"off_balance_account",
	"direction",
	"quantity",
	"uom",
	"amount",
	"currency",
	"party_type",
	"party",
	"item_code",
	"warehouse",
	"batch_no",
	"serial_no",
	"reference_doctype",
	"reference_name",
	"reference_detail",
	"external_reference_key",
	"remarks",
)


def validate_off_balance_account(account: str, company: str) -> dict:
	row = frappe.db.get_value(
		"Account",
		account,
		["company", "account_number", "is_group", "disabled", "ua_off_balance"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Рахунок {0} не знайдено").format(frappe.bold(account)))
	if row.company != company:
		frappe.throw(_("Позабалансовий рахунок має належати компанії документа"))
	if cint(row.is_group):
		frappe.throw(_("Проводку не можна створити на груповому рахунку"))
	if not cint(row.ua_off_balance) or not cstr(row.account_number).startswith("0"):
		frappe.throw(_("Рахунок {0} не позначено як рахунок класу 0").format(frappe.bold(account)))
	if not cint(row.disabled):
		frappe.throw(
			_("Рахунок класу 0 має бути вимкнений для стандартного GL ERPNext"),
			title=_("Небезпечне налаштування рахунку"),
		)
	return row


def assign_source_key(doc) -> str | None:
	doc.source_key = build_source_key(
		company=doc.company,
		account=doc.off_balance_account,
		direction=doc.direction,
		reference_doctype=doc.reference_doctype,
		reference_name=doc.reference_name,
		reference_detail=doc.reference_detail,
		external_reference_key=doc.external_reference_key,
	)
	return doc.source_key


def validate_entry(doc) -> None:
	validate_off_balance_account(doc.off_balance_account, doc.company)

	try:
		quantity, amount = validate_magnitudes(doc.quantity, doc.amount)
	except ValueError as exc:
		frappe.throw(_(str(exc)))
	doc.quantity = flt(quantity)
	doc.amount = flt(amount)

	if not amount:
		frappe.throw(_("Позабалансовий запис потребує облікової вартості, більшої за нуль"))
	if quantity and not doc.uom:
		frappe.throw(_("Для кількісного обліку потрібно вказати одиницю виміру"))
	if not doc.currency:
		doc.currency = frappe.db.get_value("Company", doc.company, "default_currency")
	if not doc.currency:
		frappe.throw(_("Для вартісного обліку потрібно вказати валюту"))

	if bool(doc.party_type) != bool(doc.party):
		frappe.throw(_("Тип контрагента і контрагент мають бути заповнені разом"))
	if doc.party_type:
		if not frappe.db.exists("DocType", doc.party_type) or not frappe.db.exists(doc.party_type, doc.party):
			frappe.throw(_("Контрагента {0} не знайдено").format(frappe.bold(doc.party)))

	if bool(doc.reference_doctype) != bool(doc.reference_name):
		frappe.throw(_("Тип документа-підстави і номер документа мають бути заповнені разом"))
	if doc.reference_doctype:
		if not frappe.db.exists("DocType", doc.reference_doctype):
			frappe.throw(_("Тип документа-підстави не знайдено"))
		if not frappe.db.exists(doc.reference_doctype, doc.reference_name):
			frappe.throw(_("Документ-підставу {0} не знайдено").format(frappe.bold(doc.reference_name)))

	assign_source_key(doc)
	if doc.source_key:
		existing = frappe.db.get_value(
			"UA Off Balance Entry",
			{"source_key": doc.source_key, "name": ("!=", doc.name or "")},
			"name",
		)
		if existing:
			frappe.throw(_("Для цього джерела вже існує позабалансовий запис {0}").format(frappe.bold(existing)))


def _balance_conditions(doc) -> tuple[list[str], dict]:
	conditions = [
		"docstatus = 1",
		"company = %(company)s",
		"off_balance_account = %(off_balance_account)s",
	]
	values = {
		"company": doc.company,
		"off_balance_account": doc.off_balance_account,
	}
	for fieldname in ("party_type", "party", "item_code", "warehouse", "batch_no", "serial_no", "uom", "currency"):
		conditions.append(f"COALESCE({fieldname}, '') = %({fieldname})s")
		values[fieldname] = cstr(doc.get(fieldname) or "")
	if doc.name:
		conditions.append("name != %(name)s")
		values["name"] = doc.name
	return conditions, values


def get_available_balance(doc, *, lock: bool = False) -> tuple[float, float]:
	conditions, values = _balance_conditions(doc)
	where = " AND ".join(conditions)
	if lock:
		frappe.db.sql(f"SELECT name FROM `tabUA Off Balance Entry` WHERE {where} FOR UPDATE", values)
	row = frappe.db.sql(
		f"""
		SELECT
			COALESCE(SUM(CASE WHEN direction = 'Increase' THEN quantity ELSE -quantity END), 0),
			COALESCE(SUM(CASE WHEN direction = 'Increase' THEN amount ELSE -amount END), 0)
		FROM `tabUA Off Balance Entry`
		WHERE {where}
		""",
		values,
	)[0]
	return flt(row[0]), flt(row[1])


def validate_decrease_balance(doc) -> None:
	if doc.direction != "Decrease":
		return
	available_quantity, available_amount = get_available_balance(doc, lock=True)
	try:
		validate_available_balance(
			available_quantity=available_quantity,
			available_amount=available_amount,
			requested_quantity=doc.quantity,
			requested_amount=doc.amount,
		)
	except ValueError as exc:
		frappe.throw(_(str(exc)), title=_("Недостатній позабалансовий залишок"))


def validate_increase_cancellation(doc) -> None:
	if doc.direction != "Increase":
		return
	remaining_quantity, remaining_amount = get_available_balance(doc, lock=True)
	if remaining_quantity < -0.000001 or remaining_amount < -0.000001:
		frappe.throw(
			_("Спочатку скасуйте або сторнуйте пізніші вибуття з цього аналітичного залишку"),
			title=_("Скасування створить від'ємний залишок"),
		)


def _normalize_value(fieldname: str, value: Any) -> Any:
	if fieldname == "posting_date":
		return cstr(getdate(value)) if value else ""
	if fieldname in {"quantity", "amount"}:
		return flt(value)
	return cstr(value or "")


def _assert_idempotent_retry(existing, payload: dict) -> None:
	changed = []
	for fieldname in ENTRY_FIELDS:
		if fieldname not in payload or payload[fieldname] is None:
			continue
		if _normalize_value(fieldname, existing.get(fieldname)) != _normalize_value(fieldname, payload[fieldname]):
			changed.append(fieldname)
	if changed:
		frappe.throw(
			_("Ключ ідемпотентності вже використано з іншими значеннями: {0}").format(", ".join(changed)),
			title=_("Конфлікт позабалансового запису"),
		)
	if existing.docstatus == 2:
		frappe.throw(
			_("Позабалансовий запис {0} з цим ключем скасовано; створіть окремий запис сторно").format(
				frappe.bold(existing.name)
			)
		)


def create_off_balance_entry(payload: dict, *, ignore_permissions: bool = False):
	values = {fieldname: payload.get(fieldname) for fieldname in ENTRY_FIELDS if fieldname in payload}
	values.setdefault("posting_date", nowdate())
	doc = frappe.get_doc({"doctype": "UA Off Balance Entry", **values})
	assign_source_key(doc)
	if not doc.source_key:
		frappe.throw(_("API-запис потребує external_reference_key або документа-підстави"))

	existing_name = frappe.db.get_value("UA Off Balance Entry", {"source_key": doc.source_key}, "name")
	if existing_name:
		existing = frappe.get_doc("UA Off Balance Entry", existing_name)
		if not ignore_permissions:
			existing.check_permission("read")
		_assert_idempotent_retry(existing, values)
		return existing

	doc.insert(ignore_permissions=ignore_permissions)
	doc.flags.ignore_permissions = ignore_permissions
	doc.submit()
	return doc


@frappe.whitelist()
def post_off_balance_entry(payload: dict | str) -> dict:
	payload = frappe.parse_json(payload) if isinstance(payload, str) else payload
	if not isinstance(payload, dict):
		frappe.throw(_("Очікується JSON-об'єкт позабалансового запису"))
	doc = create_off_balance_entry(payload)
	return {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"source_key": doc.source_key,
	}
