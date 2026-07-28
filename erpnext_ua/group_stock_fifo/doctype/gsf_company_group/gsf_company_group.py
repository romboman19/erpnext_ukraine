"""Controller for GSF Company Group (§9.3)."""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_ua.group_stock_fifo.services.domain import GroupMemberFacts, GSFError, validate_group


class GSFCompanyGroup(Document):
    def validate(self) -> None:
        members = [
            GroupMemberFacts(
                company=row.company,
                enabled=bool(row.enabled),
                can_source_stock=bool(row.can_source_stock),
                can_sell_stock=bool(row.can_sell_stock),
                base_currency=frappe.db.get_value("Company", row.company, "default_currency"),
            )
            for row in self.members
        ]
        try:
            validate_group(
                members,
                group_currency=self.base_currency,
                reporting_parent_company=self.reporting_parent_company,
            )
        except GSFError as error:
            frappe.throw(str(error), title=error.code)
