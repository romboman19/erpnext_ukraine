from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.ua_item_specs.domain import OPTION_TYPES, is_valid_fieldname


class UAItemSpecification(Document):
	def validate(self):
		self._validate_fieldname()
		self._validate_options()
		self._guard_type_change()

	def on_update(self):
		# Cached category sets carry each specification's type, unit and options,
		# so any master change invalidates them.
		from erpnext_ua.ua_item_specs.service import clear_specification_cache

		clear_specification_cache()

	def on_trash(self):
		if self._used_in_items():
			frappe.throw(
				_("Характеристику «{0}» використано в товарах. Замість видалення зніміть ознаку «Активна»").format(
					self.name
				)
			)
		if frappe.db.exists("UA Item Group Specification", {"specification": self.name}):
			frappe.throw(
				_("Характеристику «{0}» включено в набір товарної категорії. Спершу приберіть її з категорії").format(
					self.name
				)
			)
		from erpnext_ua.ua_item_specs.service import clear_specification_cache

		clear_specification_cache()

	def _validate_fieldname(self):
		self.fieldname = str(self.fieldname or "").strip().lower()
		if not is_valid_fieldname(self.fieldname):
			frappe.throw(
				_(
					"Технічний слаг «{0}» некоректний: дозволені латинські літери, цифри й підкреслення, "
					"а починатися має з літери"
				).format(self.fieldname)
			)
		before = self.get_doc_before_save()
		if before and before.fieldname and before.fieldname != self.fieldname:
			frappe.throw(_("Технічний слаг не можна змінювати: на нього спираються API, імпорт та інтеграції"))

	def _validate_options(self):
		"""Options of a non-option type are kept, not cleared — switching the type back
		must not silently destroy a list somebody curated."""
		if self.field_type not in OPTION_TYPES:
			return
		seen: set[str] = set()
		for row in self.options or []:
			value = str(row.option_value or "").strip()
			if not value:
				frappe.throw(_("Рядок {0}: значення не може бути порожнім").format(row.idx))
			if value in seen:
				frappe.throw(_("Значення «{0}» повторюється у списку дозволених").format(value))
			seen.add(value)
			row.option_value = value
		if not any(int(row.is_active or 0) for row in self.options or []):
			frappe.throw(
				_("Для типу {0} потрібно задати щонайменше одне активне значення").format(self.field_type)
			)

	def _guard_type_change(self):
		before = self.get_doc_before_save()
		if not before or before.field_type == self.field_type:
			return
		if self._used_in_items():
			frappe.throw(
				_(
					"Тип характеристики «{0}» не можна змінити: вона вже має значення в товарах, "
					"які зберігаються в колонці попереднього типу. Створіть нову характеристику "
					"або виконайте конвертацію патчем"
				).format(self.name)
			)

	def _used_in_items(self) -> bool:
		return bool(frappe.db.exists("UA Item Specification Value", {"specification": self.name}))
