"""Controller for GSF Physical Stock Count (§20.3).

The document's job is to make a difference *legible* before anyone acts on it.
§20.1's example is the whole point: three companies' pools plus whatever the
commission domain holds, counted as one shelf, differing by one unit. Which
company is short is not a detail — it decides whose books take the write-off.

So the domains are recorded apart and never summed into a single system figure
that hides them, and §20.2 forbids distributing the difference automatically:
the production default is `MANUAL_APPROVAL`, and that is enforced here rather
than left as advice.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class GSFPhysicalStockCount(Document):
    def validate(self) -> None:
        self.refresh_system_balances()
        if self.status == "APPROVED" and not self.approved_by:
            self.approved_by = frappe.session.user
            self.approved_at = now_datetime()
        if self.status == "ADJUSTED":
            frappe.throw(
                "GSF does not post count adjustments yet. §20.2 forbids automatic "
                "distribution, and the controlled Stock Reconciliation path §20.3 asks for "
                "is not implemented — record the approved plan and post it by hand.",
                title="MANUAL_REVIEW_REQUIRED",
            )

    def refresh_system_balances(self) -> None:
        """Recompute the per-domain picture from the ledger, every save.

        Recomputed rather than stored once, because a count that sat in draft
        for an hour would otherwise be compared against an hour-old system
        figure and produce a difference nobody can reproduce.
        """
        self.lines = []
        totals = {"GSF": 0.0, "CC": 0.0, "EXTERNAL": 0.0, "OTHER": 0.0}

        for row in self._warehouse_balances():
            domain = row.manager_app if row.manager_app in totals else "OTHER"
            totals[domain] += float(row.qty or 0)
            self.append(
                "lines",
                {
                    "domain": domain,
                    "company": row.company,
                    "warehouse": row.warehouse,
                    "warehouse_role": row.warehouse_role,
                    "system_qty": row.qty,
                },
            )

        self.gsf_total = totals["GSF"]
        self.cc_total = totals["CC"]
        self.external_total = totals["EXTERNAL"] + totals["OTHER"]
        self.system_total = sum(totals.values())
        self.difference = float(self.counted_qty or 0) - self.system_total

    def _warehouse_balances(self):
        """Every registered warehouse at this location that holds this item."""
        return frappe.db.sql(
            """
            select binding.warehouse, binding.company, binding.manager_app,
                   binding.warehouse_role,
                   coalesce(sum(sle.actual_qty), 0) as qty
            from `tabGSF Warehouse Binding` binding
            left join `tabStock Ledger Entry` sle
                   on sle.warehouse = binding.warehouse
                  and sle.item_code = %(item)s
                  and sle.is_cancelled = 0
            where binding.enabled = 1
              and binding.physical_location = %(location)s
            group by binding.warehouse, binding.company, binding.manager_app,
                     binding.warehouse_role
            order by binding.manager_app, binding.company, binding.warehouse
            """,
            {
                "item": self.item_code,
                "location": self.physical_location,
            },
            as_dict=True,
        )
