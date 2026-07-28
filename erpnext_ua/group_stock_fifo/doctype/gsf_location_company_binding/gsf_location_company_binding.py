"""Controller for GSF Location Company Binding (§6.3)."""

from __future__ import annotations

import frappe
from frappe.model.document import Document


class GSFLocationCompanyBinding(Document):
    def validate(self) -> None:
        self._assert_unique()
        self._assert_member()
        self.base_currency_snapshot = frappe.db.get_value(
            "Company", self.company, "default_currency"
        )

    def _assert_unique(self) -> None:
        duplicate = frappe.db.exists(
            "GSF Location Company Binding",
            {
                "company_group": self.company_group,
                "physical_location": self.physical_location,
                "company": self.company,
                "name": ("!=", self.name or ""),
            },
        )
        if duplicate:
            frappe.throw(
                f"{self.company} is already bound to {self.physical_location}",
                title="LOCATION_NOT_ACTIVE",
            )

    def _assert_member(self) -> None:
        is_member = frappe.db.exists(
            "GSF Group Member", {"parent": self.company_group, "company": self.company}
        )
        if not is_member:
            frappe.throw(
                f"{self.company} is not a member of {self.company_group}",
                title="COMPANY_NOT_GROUP_MEMBER",
            )
