import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime


def _bounds(row):
    return (
        get_datetime(row.valid_from) if row.valid_from else get_datetime("1900-01-01"),
        get_datetime(row.valid_to) if row.valid_to else get_datetime("2999-12-31"),
    )


class UALoyaltyLocation(Document):
    def validate(self):
        if not any((self.pos_cash_desk, self.branch, self.warehouse, self.company)):
            frappe.throw("Вкажіть касу, філію, склад або компанію")
        if self.pos_cash_desk:
            rows = frappe.get_all(
                "UA Loyalty Location",
                filters={
                    "pos_cash_desk": self.pos_cash_desk,
                    "active": 1,
                    "name": ("!=", self.name or ""),
                },
                fields=["valid_from", "valid_to"],
            )
            own_start, own_end = _bounds(self)
            if own_start > own_end:
                frappe.throw("valid_from не може бути пізніше valid_to")
            if any(start <= own_end and own_start <= end for start, end in map(_bounds, rows)):
                frappe.throw("Для цієї каси вже існує overlapping mapping області лояльності")
