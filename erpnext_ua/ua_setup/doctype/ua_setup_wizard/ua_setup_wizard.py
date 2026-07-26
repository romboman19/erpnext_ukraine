from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.ua_setup import service


class UASetupWizard(Document):
	"""Форма-майстер: збирає дані платника й делегує роботу сервісам.

	Самі кроки живуть у ``erpnext_ua.ua_setup.service`` і не залежать від цієї
	форми — ту саму послідовність можна виконати з bench або з API.
	"""

	def onload(self):
		if not self.company:
			self.company = service._default_company()

	@frappe.whitelist()
	def run_step(self, step: str) -> dict:
		self.check_permission("write")
		handler = getattr(self, f"_step_{step}", None)
		if handler is None:
			frappe.throw(_("Невідомий крок налаштування: {0}").format(step))
		result = handler()
		self.save(ignore_permissions=True)
		return result

	def _require(self, *fieldnames: str) -> None:
		missing = [
			_(self.meta.get_label(fieldname)) for fieldname in fieldnames if not (self.get(fieldname) or "").strip()
		]
		if missing:
			frappe.throw(_("Заповніть обов'язкові поля кроку: {0}").format(", ".join(missing)))

	def _step_apply_language(self) -> dict:
		return service.apply_language()

	def _step_apply_tax_parameters(self) -> dict:
		return service.apply_tax_parameters()

	def _step_apply_payment_methods(self) -> dict:
		return service.apply_payment_methods()

	def _step_apply_chart(self) -> dict:
		self._require("company", "chart_template")
		return service.apply_chart(self.company, self.chart_template)

	def _step_apply_fop_profile(self) -> dict:
		self._require(
			"company",
			"fop_full_name",
			"prro_registered_name",
			"tax_id",
			"single_tax_group",
			"tax_rate_mode",
		)
		result = service.apply_fop_profile(
			company=self.company,
			fop_full_name=self.fop_full_name,
			prro_registered_name=self.prro_registered_name,
			tax_id=self.tax_id,
			single_tax_group=self.single_tax_group,
			tax_rate_mode=self.tax_rate_mode,
			kved_main=self.kved_main,
			registration_address=self.registration_address,
			iban=self.iban,
			vat_payer=self.vat_payer,
			vat_number=self.vat_number,
		)
		self.fop_profile = result["fop_profile"]
		return result

	def _step_apply_cash_desk(self) -> dict:
		self._require("company")
		result = service.apply_cash_desk(self.company, self.warehouse, self.desk_name)
		self.warehouse = frappe.db.get_value("POS Cash Desk", result["cash_desk"], "warehouse")
		return result

	def _step_apply_prro_register(self) -> dict:
		self._require(
			"fop_profile",
			"register_name",
			"fiscal_number",
			"register_local_number",
			"unit_name",
			"unit_address",
		)
		return service.apply_prro_register(
			fop_profile=self.fop_profile,
			register_name=self.register_name,
			fiscal_number=self.fiscal_number,
			register_local_number=self.register_local_number,
			unit_name=self.unit_name,
			unit_address=self.unit_address,
		)
