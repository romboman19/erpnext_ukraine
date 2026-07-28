# ADR 0005 — Idempotency and stable keys

## Status

Accepted on 2026-07-27, after gate 0e.

## Context

A GSF checkout submits several documents in sequence, across two companies, and
may be retried after a network failure, a PRRO timeout or a worker restart.
[ADR-012](0012-pos-prro-saga.md) requires that a retry reuse the
existing route and execute only the missing steps, and states that "stable keys
are mandatory" — without saying what may serve as one.

## Evidence

Gate 0e ([evidence](../spikes/evidence/2026-07-27-gate-0e-atomic-rollback.md))
answered the question by accident, and the accident is the evidence.

The gate's first run reported `FAIL` on four surviving ledger rows. None of them
had survived anything. Two separate platform behaviours combined:

1. Cancelling a Stock Entry sets `is_cancelled = 1` on its Stock Ledger Entry
   and GL Entry rows rather than deleting them. Deleting the parent document
   afterwards leaves those rows orphaned — 38 of them had accumulated against a
   single item before the cleanup was written.
2. `frappe.delete_doc` on the newest document calls `revert_series_if_last`,
   which winds the naming series counter back. The next run was therefore handed
   `MAT-STE-2026-00002` and `MAT-STE-2026-00003` again — the same names the
   previous run had used.

A survivor check keyed on `voucher_no` matched the previous run's cancelled rows
and reported them as the current run's leaks. The rollback itself was correct
all along: balances were restored exactly and no live ledger row survived.

## Decision

**ERPNext document names are not stable identifiers and may not be used as
idempotency keys.** A name that has been issued and deleted comes back. Any GSF
key derived from `Stock Entry.name`, `Sales Invoice.name` or a naming series
counter is a defect against this ADR.

**GSF idempotency keys are domain-owned request fingerprints**, following the
commission module's existing `pos_checkout_fingerprint` — a hash over the
semantic content of the request (checkout identity, company, location, item,
quantity, allocation slices) rather than over anything the platform assigns.
The key is computed before the first document is created and stored on the route.

**A route row is created before the documents it will own.** Retry reuses the
route and executes only steps whose documents are absent, matching on the
domain key rather than on document names.

**Reconciliation and diagnostics filter `is_cancelled = 0`.** Cancelled ledger
rows persist after their parent is deleted, so any query that counts or sums
ledger rows without this filter reads history as if it were current state. This
is a query rule for the whole domain, not a detail of one gate.

## Consequences

- Cross-run and cross-retry evidence must be compared by row identity, not by
  voucher name. The spike suite already does this — it diffs the set of live
  ledger row names around a savepoint — and Phase 1 regression tests inherit the
  same rule.
- Orphaned cancelled ledger rows are a real housekeeping concern on any site
  that deletes cancelled stock documents. GSF does not delete submitted
  documents in production, so it does not create them, but the diagnostics
  should be able to report them.
- The fingerprint must be stable across process restarts and across workers,
  which rules out anything derived from in-memory state or wall-clock time.
- Because [ADR-012](0012-pos-prro-saga.md) leaves payment and
  fiscalization with `POS Order`, GSF's key covers stock preparation only. It
  must not be reused as, or confused with, the fiscal idempotency key.
