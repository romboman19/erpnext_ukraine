"""Controller for GSF Checkout Line.

One user-visible basket line. Its `allocation` is filled once reservation
succeeds, which is also what makes the reserve step resumable: a line that
already names an allocation has been done.
"""

from __future__ import annotations

from frappe.model.document import Document


class GSFCheckoutLine(Document):
    pass
