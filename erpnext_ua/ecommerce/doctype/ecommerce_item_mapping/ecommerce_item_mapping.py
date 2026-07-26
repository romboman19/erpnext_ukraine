from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_ua.integrations.utils.operations import canonical_hash

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EcommerceItemMapping(Document):
    def validate(self):
        self.channel = (self.channel or "").strip()
        self.external_sku = (self.external_sku or "").strip()
        self.external_id = (self.external_id or self.external_sku or self.item or "").strip()
        self.variant_sku = (self.variant_sku or self.external_sku or self.item or "").strip()
        if not self.channel or not self.external_id:
            frappe.throw(_("Channel and External ID are required"))
        if self.last_export_hash and not _SHA256.fullmatch(self.last_export_hash):
            frappe.throw(_("Last Export Hash must be a lowercase SHA-256 value"))
        if self.export_hash_state:
            try:
                state = json.loads(self.export_hash_state)
            except (TypeError, ValueError):
                frappe.throw(_("Export Hash State must be valid JSON"))
            if not isinstance(state, dict) or any(
                not isinstance(entity, str)
                or not isinstance(digest, str)
                or not _SHA256.fullmatch(digest)
                for entity, digest in state.items()
            ):
                frappe.throw(_("Export Hash State contains an invalid payload hash"))
            self.export_hash_state = json.dumps(
                state,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        self.mapping_key = canonical_hash({"channel": self.channel, "item": self.item})
        self.external_mapping_key = canonical_hash(
            {"channel": self.channel, "external_id": self.external_id}
        )
        if not self.sync_status:
            self.sync_status = "Pending"
