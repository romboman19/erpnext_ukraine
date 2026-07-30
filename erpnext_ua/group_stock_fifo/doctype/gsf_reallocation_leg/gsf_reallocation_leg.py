"""Controller for GSF Reallocation Leg (§9.15).

One source company's move into the seller's stage. `counterparty_company` is
the reconciliation key §31.6 needs — it stands in for the accounting dimension
ERPNext refuses to create over `Company` (ADR-005 amendment), so it must be set
on every cross-company leg or the balances become unreconcilable by counterparty.
"""

from __future__ import annotations

from frappe.model.document import Document


class GSFReallocationLeg(Document):
    pass
