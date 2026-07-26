from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.ua_setup import service


class UASetupWizard(Document):
	"""Стан налаштування для однієї компанії. Нічого не зберігає постійно.

	Це Single DocType, тобто один рядок на весь сайт — тому поле ``company`` і
	результати кроків ніколи не проходять через ``self.save()``. Раніше форма
	зберігала введені дані ФОП/каси при кожному кроці; для другої компанії
	лишалися значення від першої, бо запис один на сайт, а не на компанію.

	Кроки, у яких є власний повноцінний DocType (``FOP Profile``,
	``UA Chart of Accounts Setup``, ``PRRO Cash Register``), майстер більше не
	дублює: кнопка веде на форму створення. Тут лишається лише те, що не має
	власної форми або отримує реальну користь від об'єднання кількох дій
	(``apply_cash_desk`` заразом створює роздрібного покупця).
	"""

	def onload(self):
		if not self.company:
			self.company = service._default_company()

	@frappe.whitelist()
	def run_step(self, step: str, args: dict | None = None) -> dict:
		self.check_permission("write")
		handler = getattr(self, f"_step_{step}", None)
		if handler is None:
			frappe.throw(_("Невідомий крок налаштування: {0}").format(step))
		return handler(frappe.parse_json(args) if isinstance(args, str) else (args or {}))

	def _step_apply_language(self, args: dict) -> dict:
		return service.apply_language()

	def _step_apply_tax_parameters(self, args: dict) -> dict:
		return service.apply_tax_parameters()

	def _step_apply_payment_methods(self, args: dict) -> dict:
		return service.apply_payment_methods()

	def _step_apply_cash_desk(self, args: dict) -> dict:
		company = args.get("company") or self.company
		if not company:
			frappe.throw(_("Спершу оберіть компанію"))
		return service.apply_cash_desk(company, args.get("warehouse"), args.get("desk_name"))
