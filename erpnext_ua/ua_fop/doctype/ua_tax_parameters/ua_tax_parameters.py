import frappe
from frappe.model.document import Document


class UATaxParameters(Document):
	def validate(self):
		if self.year and (self.year < 2020 or self.year > 2100):
			frappe.throw("Некоректний рік")
		exists = frappe.db.exists(
			"UA Tax Parameters",
			{"year": self.year, "single_tax_group": self.single_tax_group, "name": ("!=", self.name)},
		)
		if exists:
			frappe.throw(f"Параметри для {self.year} року, група {self.single_tax_group} вже існують: {exists}")
