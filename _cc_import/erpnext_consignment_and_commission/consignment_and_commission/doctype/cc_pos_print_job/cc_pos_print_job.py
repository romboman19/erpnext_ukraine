import frappe
from frappe.model.document import Document

WRITE_FLAG = "cc_pos_print_job_service"
TEST_CLEANUP_FLAG = "cc_pos_print_job_test_cleanup"


class CCPOSPrintJob(Document):
    def validate(self) -> None:
        if not getattr(frappe.flags, WRITE_FLAG, False):
            frappe.throw("CC POS Print Job is server-owned")
        if self.print_kind not in {"FISCAL_RECEIPT", "NON_FISCAL_GOODS_RECEIPT"}:
            frappe.throw("CC POS Print Job has an unsupported print kind")
        if int(self.attempts or 0) < 0:
            frappe.throw("CC POS Print Job attempts cannot be negative")

    def on_trash(self) -> None:
        if not frappe.in_test and not getattr(frappe.flags, TEST_CLEANUP_FLAG, False):
            frappe.throw("CC POS Print Job is immutable operational evidence")
