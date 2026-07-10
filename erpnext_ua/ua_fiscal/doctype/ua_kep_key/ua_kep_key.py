import frappe
from frappe.model.document import Document


class UAKEPKey(Document):
	def validate(self):
		self.validate_key_file_is_private()
		self.update_status_by_validity()

	def validate_key_file_is_private(self):
		if not self.key_file:
			return
		if not self.key_file.startswith("/private/"):
			frappe.throw(
				"Файл ключа має бути приватним. Завантажте його ще раз, "
				"вимкнувши позначку «Public» при завантаженні."
			)

	def update_status_by_validity(self):
		if (
			self.status == "Active"
			and self.valid_until
			and frappe.utils.getdate(self.valid_until) < frappe.utils.getdate()
		):
			self.status = "Expired"
