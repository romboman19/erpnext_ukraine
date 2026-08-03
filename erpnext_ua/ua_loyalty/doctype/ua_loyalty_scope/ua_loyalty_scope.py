from frappe.model.document import Document


class UALoyaltyScope(Document):
    def validate(self):
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            self.valid_to, self.valid_from = self.valid_from, self.valid_to
