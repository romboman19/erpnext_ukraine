import frappe
from frappe.model.document import Document


class PRROReceipt(Document):
	def validate(self):
		if self.receipt_type == "Повернення" and not self.related_receipt:
			frappe.msgprint(
				"Для чека повернення бажано вказати повʼязаний чек продажу",
				indicator="orange",
				alert=True,
			)
