# ADR 0008 — Transaction boundary

## Status

Accepted on 2026-07-27, after gate 0e. Numbered per [spec](../spec-v1.0.md) §40.

## Context

§14.6 requires stock preparation and the ERP sale to run in one transaction, and
forbids `frappe.db.commit()` inside a domain service. §23.2 draws the line
between rollback and compensation. §37.14 demands proof that an exception before
the destination receipt leaves no source issue behind.

## Evidence

Gate 0e ([evidence](../spikes/evidence/2026-07-27-gate-0e-atomic-rollback.md))
submitted the full chain inside a savepoint — source Material Issue, destination
Material Receipt, Sales Invoice with `update_stock=1` — then raised on purpose
after the last one.

| Check | Result |
|---|---|
| injected failure raised | ✅ |
| no document survived | ✅ |
| no live ledger row survived | ✅ |
| balances restored exactly | ✅ |
| seed layer intact | ✅ |

`frappe.db.rollback(save_point=...)` removed the Stock Ledger Entries and GL
Entries together with their documents. Reproduced across two consecutive runs.

## Decision

**Rollback to a savepoint is the compensation mechanism for everything before
the ERP commit.** No manual reversal documents, no ledger cleanup, no
compensating Journal Entry for the stock legs. §23.2's "rollback" branch is
fully realised by the platform.

**Compensation begins only after the ERP transaction commits**, per §23.2 — that
is, once an external side effect (payment capture, PRRO) may have occurred. From
that point reversal documents are created and audit evidence is never deleted
(§34.3, §44).

**No domain service calls `frappe.db.commit()`.** §14.6 is adopted verbatim; the
gate's own runner commits only at its outermost boundary, and production must do
the same.

## Consequences

- The `PREPARING_STOCK → FAILED` edge in §23 is a plain rollback and needs no
  document machinery.
- Two behaviours observed during the gate constrain how failures are diagnosed,
  and are recorded in
  [ADR-014](0014-idempotency-and-stable-keys.md): cancelled ledger rows outlive
  a deleted parent, and Frappe reverts the naming series when the newest
  document is deleted.
- Not covered: a process crash between submit and commit. The gate proves
  in-process rollback, which is what the checkout saga does; an aborted worker
  is a recovery-scan concern, not a transaction-boundary one.
- The test stack has no scheduler, so any `Repost Item Valuation` ERPNext might
  enqueue was never executed. That interaction needs checking on a stack with a
  scheduler before production.
