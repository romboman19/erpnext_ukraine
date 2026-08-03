import frappe
from frappe.model.document import Document


class PRROFiscalizationJob(Document):
    def on_trash(self):
        if self.status not in {"Pending", "Cancelled"}:
            frappe.throw("Завершене або розпочате завдання фіскалізації видаляти не можна")
