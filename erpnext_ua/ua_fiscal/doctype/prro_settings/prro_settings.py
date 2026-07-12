import frappe
from frappe.model.document import Document


class PRROSettings(Document):
	def get_fiscal_server_url(self) -> str:
		# ЄВПЕЗ: один REST-ендпоінт для тесту й бою; режим керує лише прапорцем
		# <TESTING> у документах, а не адресою сервера.
		if not self.fiscal_server_url:
			frappe.throw("Не задано URL фіскального сервера ДПС")
		return self.fiscal_server_url.rstrip("/")
