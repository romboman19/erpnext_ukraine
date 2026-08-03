import frappe
from frappe.model.document import Document


class UATaxDeadline(Document):
	def validate(self):
		if not self.fop_profile:
			self.fop_profile = frappe.db.get_value("FOP Profile", {"company": self.company})
		# Legacy rows predate the separate statutory date. Preserve their recorded
		# operational date until the calendar refresher can match a rule precisely.
		if not self.statutory_due_date and self.due_date:
			self.statutory_due_date = self.due_date
