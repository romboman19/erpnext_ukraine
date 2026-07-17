import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from erpnext_ua.ua_accounting.chart_of_accounts import load_template, template_summary
from erpnext_ua.ua_accounting.chart_setup import apply_chart, preflight


class UAChartofAccountsSetup(Document):
	def validate(self):
		template = load_template(self.chart_template)
		self.template_revision = max(row["revision"] for row in template["legal_basis"])
		if self.is_new() and not self.status:
			self.status = "Draft"

	@frappe.whitelist()
	def preview(self):
		frappe.only_for(["Accounts Manager", "System Manager"])
		result = preflight(self.company, self.chart_template)
		self.status = "Ready" if result["allowed"] else "Blocked"
		self.last_check = now_datetime()
		self.result_log = frappe.as_json(result, indent=2)
		self.save(ignore_permissions=True)
		return result

	@frappe.whitelist()
	def apply_template(self):
		result = apply_chart(self.company, self.chart_template, self.confirm_company_name)
		self.status = "Applied"
		self.applied_on = now_datetime()
		self.applied_by = frappe.session.user
		self.confirm_company_name = None
		self.result_log = frappe.as_json(result, indent=2)
		self.save(ignore_permissions=True)
		return result

	@frappe.whitelist()
	def get_template_summary(self):
		return template_summary(self.chart_template)
