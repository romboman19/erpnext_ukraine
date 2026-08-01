# ADR-001: Domain boundary

Accepted. Gift certificates live inside `erpnext_ua` but keep calculations/services independent from UI, ERPNext core and external devices. Adapters translate only at boundaries.
