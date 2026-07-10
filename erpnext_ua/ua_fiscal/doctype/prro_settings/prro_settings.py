import frappe
from frappe.model.document import Document


class PRROSettings(Document):
	def get_fiscal_server_url(self) -> str:
		url = self.fiscal_server_url if self.mode == "Бойовий" else self.fiscal_server_test_url
		if not url:
			frappe.throw(f"Не задано URL фіскального сервера для режиму «{self.mode}»")
		return url.rstrip("/")
