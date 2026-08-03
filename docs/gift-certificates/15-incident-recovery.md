# Incident recovery

- Card timeout: keep Payment Unknown; query durable terminal evidence, never repeat sale blindly.
- SI submitted/consume missing: recovery calls idempotent consume against the same invoice.
- Fiscal timeout: keep Fiscal Pending and reconcile the existing PRRO UID before retry.
- Print failure: financial sale remains complete; a one-time program uses replacement, not token reuse.
- Reconciliation mismatch: freeze affected certificate, capture evidence, rebuild cache only when ledger is complete; otherwise use approved adjustment/reversal.
