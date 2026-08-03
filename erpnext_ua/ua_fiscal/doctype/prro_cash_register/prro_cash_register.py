import hashlib
import re
import uuid

import frappe
from frappe.model.document import Document


class PRROCashRegister(Document):
	def validate(self):
		if not self.device_id:
			self.device_id = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
		if not re.fullmatch(r"[0-9a-f]{64}", self.device_id or ""):
			frappe.throw("Device ID ПРРО має бути 64-символьним SHA-256 hex значенням")
		if int(self.register_local_number or 0) <= 0:
			frappe.throw("Локальний номер ПРРО з форми 1-ПРРО має бути більшим за нуль")
		if int(self.next_local_number or 0) <= 0:
			self.next_local_number = 1
		if self.pos_profile:
			exists = frappe.db.exists(
				"PRRO Cash Register",
				{"pos_profile": self.pos_profile, "name": ("!=", self.name), "status": "Active"},
			)
			if exists and self.status == "Active":
				frappe.throw(f"POS Profile {self.pos_profile} вже привʼязаний до активної каси {exists}")
		if int(self.get("ecommerce_default") or 0) and self.status == "Active":
			exists = frappe.db.exists(
				"PRRO Cash Register",
				{
					"company": self.company,
					"ecommerce_default": 1,
					"name": ("!=", self.name),
					"status": "Active",
				},
			)
			if exists:
				frappe.throw(
					f"Для компанії {self.company} вже налаштована ecommerce-каса ПРРО {exists}"
				)

	def allocate_local_number(self) -> int:
		"""Атомарно видає наступний локальний номер документа."""
		number = frappe.db.get_value("PRRO Cash Register", self.name, "next_local_number", for_update=True)
		if not number or number <= 0:
			number = 1
		frappe.db.set_value(
			"PRRO Cash Register", self.name, "next_local_number", number + 1, update_modified=False
		)
		self.next_local_number = number + 1
		return number

	def on_trash(self):
		if frappe.db.exists("PRRO Shift", {"cash_register": self.name}):
			frappe.throw("Касу ПРРО з історією змін видаляти не можна; переведіть її у статус Disabled")
