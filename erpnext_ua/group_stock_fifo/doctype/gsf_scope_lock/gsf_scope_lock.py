"""Controller for GSF Scope Lock.

The row carries no state: it exists so that level 2 of the §13.2 lock order —
`group/location/item` — is a real object a transaction can take a row lock on,
in the same order as every other service.
"""

from __future__ import annotations

from frappe.model.document import Document


class GSFScopeLock(Document):
    pass
