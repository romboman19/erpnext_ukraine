"""Controller for GSF Stock Count Line.

One domain's balance in one warehouse. §20.3 requires the domains to be shown
apart rather than summed, because a difference against a total tells nobody
whose stock is missing.
"""

from __future__ import annotations

from frappe.model.document import Document


class GSFStockCountLine(Document):
    pass
