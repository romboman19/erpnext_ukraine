import frappe
from frappe.model.document import Document

from erpnext_ua.ua_accounting.off_balance import (
	assign_source_key,
	validate_decrease_balance,
	validate_entry,
	validate_increase_cancellation,
)


class UAOffBalanceEntry(Document):
	def before_insert(self):
		if not self.external_reference_key and not (self.reference_doctype and self.reference_name):
			self.external_reference_key = f"manual:{frappe.generate_hash(length=20)}"
		assign_source_key(self)

	def validate(self):
		validate_entry(self)

	def before_submit(self):
		validate_decrease_balance(self)

	def before_cancel(self):
		validate_increase_cancellation(self)
