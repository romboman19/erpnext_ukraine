import frappe
from frappe.model.document import Document


class PRROReceipt(Document):
	def validate(self):
		if self.receipt_kind == "Return" and not self.related_receipt:
			frappe.throw("Для фіскального чека повернення обовʼязково вкажіть первинний чек")

	def on_trash(self):
		if self.status not in {"Draft", "Cancelled"}:
			frappe.throw("Фіскальний журнал є незмінним; доставлені, офлайн або невизначені документи видаляти не можна")


@frappe.whitelist()
def receipt_preview(name: str) -> dict:
	doc = frappe.get_doc("PRRO Receipt", name)
	if not frappe.has_permission("PRRO Receipt", "read", doc=doc):
		frappe.throw("Недостатньо прав для перегляду фіскального чека", frappe.PermissionError)
	if doc.status not in {"Fiscalized", "Offline"}:
		frappe.throw("Друк доступний лише для підтвердженого фіскального документа")
	if doc.receipt_kind in {"Open Shift", "Z Report"}:
		cash_desk = frappe.db.get_value(
			"POS Cash Desk", {"prro_cash_register": doc.cash_register, "status": "Active"}, "name"
		) or frappe.db.get_value("POS Cash Desk", {"prro_cash_register": doc.cash_register}, "name")
		if not cash_desk:
			frappe.throw("Для каси ПРРО не знайдено повʼязану касу продажу")
		from erpnext_ua.ua_pos.api import _fiscal_report_data
		from erpnext_ua.ua_pos.print_service import render_browser_fiscal_report

		report_type = "OPENING" if doc.receipt_kind == "Open Shift" else "Z"
		report = _fiscal_report_data(cash_desk, report_type, doc.shift)
		return {
			"name": doc.name,
			"title": report["title"],
			"fiscal_number": doc.fiscal_number,
			"html": render_browser_fiscal_report(report),
		}
	if doc.receipt_kind not in {"Sale", "Return", "Storno"}:
		frappe.throw("Для цього класу документа ПРРО друкована форма ще не реалізована")
	if not doc.receipt_xml:
		frappe.throw("У фіскального документа відсутній XML snapshot")

	from erpnext_ua.ua_pos.print_service import fiscal_snapshot, render_browser_fiscal_receipt

	snapshot = fiscal_snapshot(doc, include_qr_image=True)
	lookup_token = frappe.db.get_value("POS Order", doc.pos_order, "lookup_token") if doc.pos_order else None
	return {
		"name": doc.name,
		"title": "Видатковий чек" if doc.receipt_kind in {"Return", "Storno"} else "Фіскальний чек",
		"fiscal_number": doc.fiscal_number,
		"html": render_browser_fiscal_receipt(snapshot, lookup_token=lookup_token),
	}
