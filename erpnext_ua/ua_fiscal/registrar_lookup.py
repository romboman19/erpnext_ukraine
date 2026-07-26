"""Перелік зареєстрованих ПРРО/госп. одиниць з фіскального сервера ДПС.

Команда "Objects" ("Запит доступних об'єктів") — частина протоколу того самого
фіскального сервера, що вже приймає чеки й Z-звіти (``PRRO Settings.fiscal_server_url``),
а не приватного кабінету платника. Підписується КЕП конкретного ПРРО, і
повертає всі господарські одиниці та зареєстровані на них каси, доступні
цьому підписанту — незалежно від того, яку саме касу зараз створюють.
"""

from __future__ import annotations

import frappe
from frappe import _

from erpnext_ua.ua_fiscal.fiscal_client import FiscalClient


def parse_objects(payload: dict | None) -> list[dict]:
	"""Розгорнути TaxObjects/TransactionsRegistrars в один рядок на кожну касу.

	Закриті (скасовані) реєстрації ПРРО не повертаються: обирати їх для нової
	каси — завжди помилка користувача, а не легітимний варіант.
	"""
	if not payload:
		return []
	rows = []
	for unit in payload.get("TaxObjects") or []:
		for registrar in unit.get("TransactionsRegistrars") or []:
			if registrar.get("Closed"):
				continue
			rows.append(
				{
					"unit_name": unit.get("Name") or "",
					"unit_address": unit.get("Address") or "",
					"fiscal_number": str(registrar.get("NumFiscal") or ""),
					"register_local_number": registrar.get("NumLocal"),
					"register_name": registrar.get("Name") or "",
				}
			)
	return rows


@frappe.whitelist()
def list_registered_objects(kep_key: str) -> list[dict]:
	"""Господарські одиниці й каси ПРРО, вже зареєстровані в ДПС для цього КЕП."""
	frappe.only_for(["System Manager", "Accounts Manager"])
	key = frappe.get_doc("UA KEP Key", kep_key)
	key.check_permission("read")
	if key.status != "Active":
		frappe.throw(_("Обраний КЕП неактивний"))

	rows = parse_objects(FiscalClient().objects(kep_key))
	if not rows:
		frappe.throw(_("ДПС не повернула жодної зареєстрованої каси для цього КЕП"))
	return rows
