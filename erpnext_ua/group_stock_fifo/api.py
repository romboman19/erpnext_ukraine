"""Versioned read-only API surface for the foundation layer (§27.1)."""

from __future__ import annotations

from typing import Any

import frappe

from .services.readiness import as_dict as readiness_payload


@frappe.whitelist()
def diagnostics_readiness() -> dict[str, Any]:
    """`GET /diagnostics/readiness` (§27.1)."""
    frappe.only_for(("System Manager", "GSF System Manager", "GSF Auditor"))
    return readiness_payload()
