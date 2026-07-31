import frappe
from frappe.model.document import Document
from frappe.utils import flt


FINAL_STATES = {"Completed", "Completed Print Error", "Invoice Draft", "Cancelled"}


class POSOrder(Document):
    def validate(self):
        if self.order_type == "Return" and not self.return_against:
            frappe.throw("Для повернення потрібен первинний POS-чек")
        self.net_total = 0
        self.discount_total = 0
        for row in self.items or []:
            if flt(row.qty) <= 0:
                frappe.throw("Кількість товару має бути більшою за нуль")
            if flt(row.rate) < 0:
                frappe.throw("Ціна товару не може бути від’ємною")
            gross = flt(row.qty) * flt(row.rate)
            row.gross_amount = gross
            # Existing rows predate the split. Treat their sole discount as the
            # non-loyalty component so upgrades never change a historical total.
            if not row.non_loyalty_discount_amount and row.discount_amount and not row.loyalty_redeemed_amount:
                row.non_loyalty_discount_amount = row.discount_amount
            row.non_loyalty_discount_amount = min(gross, max(0, flt(row.non_loyalty_discount_amount)))
            row.amount_before_loyalty = gross - row.non_loyalty_discount_amount
            row.loyalty_redeemed_amount = min(row.amount_before_loyalty, max(0, flt(row.loyalty_redeemed_amount)))
            row.discount_amount = row.non_loyalty_discount_amount + row.loyalty_redeemed_amount
            row.amount = gross - row.discount_amount
            self.net_total += row.amount
            self.discount_total += row.discount_amount or 0
        self.grand_total = self.net_total
        self.paid_total = sum((row.amount or 0) for row in self.payments_plan or [] if row.status == "Confirmed")
        self.change_amount = sum(
            (row.change_amount or 0) for row in self.payments_plan or [] if row.status == "Confirmed"
        )

    def on_trash(self):
        if self.status in FINAL_STATES:
            frappe.throw("Final POS orders cannot be deleted")
