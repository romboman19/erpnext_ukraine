import frappe
from frappe.model.document import Document


class UAKVED(Document):
	def validate(self):
		self.code = (self.code or "").strip()
		if not frappe.utils.cstr(self.code).replace(".", "").isdigit():
			frappe.throw("Код КВЕД має складатися з цифр і крапок, наприклад 47.91")
