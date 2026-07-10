import frappe
from frappe.model.document import Document


class PRROCashRegister(Document):
	def validate(self):
		if self.pos_profile:
			exists = frappe.db.exists(
				"PRRO Cash Register",
				{"pos_profile": self.pos_profile, "name": ("!=", self.name), "status": "Active"},
			)
			if exists and self.status == "Active":
				frappe.throw(f"POS Profile {self.pos_profile} вже привʼязаний до активної каси {exists}")

	def allocate_local_number(self) -> int:
		"""Атомарно видає наступний локальний номер документа."""
		number = frappe.db.get_value("PRRO Cash Register", self.name, "next_local_number", for_update=True)
		frappe.db.set_value(
			"PRRO Cash Register", self.name, "next_local_number", number + 1, update_modified=False
		)
		return number
