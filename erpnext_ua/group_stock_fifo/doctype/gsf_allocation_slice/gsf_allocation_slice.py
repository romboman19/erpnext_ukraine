"""Controller for GSF Allocation Slice (§9.13).

Immutable once its parent allocation reaches RESERVED — the parent enforces
that, because a child row's own validate cannot see the parent's status
transition. Nothing here recomputes a cost: `reserved_stock_value_snapshot` is
informational, and the final value is read from the source SLE at preparation
time (ADR-003).
"""

from __future__ import annotations

from frappe.model.document import Document


class GSFAllocationSlice(Document):
    pass
