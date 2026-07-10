import frappe
from frappe.model.document import Document


class PRROShift(Document):
	def validate(self):
		if self.status in ("Opening", "Open"):
			other_open = frappe.db.exists(
				"PRRO Shift",
				{
					"cash_register": self.cash_register,
					"status": ("in", ("Opening", "Open", "Closing")),
					"name": ("!=", self.name),
				},
			)
			if other_open:
				frappe.throw(f"На касі вже є незакрита зміна: {other_open}")
