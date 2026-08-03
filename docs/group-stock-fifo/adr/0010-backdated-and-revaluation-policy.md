# ADR 0010 — Backdated operations and revaluation policy

## Status

Accepted on 2026-07-28. Numbered per [spec](../spec-v1.0.md) §40. Derived from
§25, with gate 0k supplying the detection mechanism.

## Context

§25.1 lists what a backdated receipt, a Landed Cost Voucher, a purchase return
or a repost can change: the global FIFO order, the local valuation queue,
historical COGS, the value of a reallocation that already posted, and the profit
of the selling company.

The last two are what make this different from ordinary ERPNext backdating. A
reallocation is a transfer between two legal entities whose value was fixed from
a ledger reading at the time (ADR-003). If that reading is later invalidated, the
two companies' books disagree about a transaction that has already happened.

## Evidence

Gate 0k ([evidence](../spikes/evidence/2026-07-28-gate-0k-valuation-preflight.md))
gave the detection mechanism: the preflight reads the queue ERPNext will actually
consume from, so a backdated document that reorders that queue becomes visible as
`VALUATION_QUEUE_DIVERGENCE` on the next issue.

That is detection, not repair, and the distinction is the whole content of this
ADR. Nothing in Phase 0 demonstrated a safe cross-company revaluation, because
none was attempted.

## Decision

**MVP blocks rather than repairs**, per §25.2:

- backdated stock documents earlier than `closed_through_date` are refused with
  `BACKDATED_OPERATION_BLOCKED`;
- an origin document cannot be amended once its layer has been transferred to
  another company or sold — `CLOSED_PERIOD` or a controlled cascade, never a
  silent rewrite (§11.4);
- a Landed Cost Voucher against a receipt with downstream cross-company movement
  is refused without a controlled workflow;
- after a `Repost Item Valuation`, GSF raises a `GSF Integrity Issue` rather than
  assuming the repost was harmless;
- completed fiscal sales are never rewritten automatically. A fiscal receipt is
  an external artefact; §44 forbids treating it as revisable state.

**The preflight is the runtime guard.** A backdated document that slipped in
before these blocks existed, or that ERPNext permitted for a warehouse GSF does
not manage, still cannot corrupt a reallocation: the next issue's preflight
compares planned against predicted and refuses on mismatch.

**`GSF Global Revaluation` (§25.3) is explicitly out of MVP.** Building it means
walking origin receipt → layer → source issue → destination receipt → stage →
sale COGS → returns, adjusting both companies' clearing positions in step,
without touching quantity history or fiscal totals. That is a phase of its own.

**Period close (§25.4) is the operational counterpart.** `GSF Period Close`
records the closing date, the integrity and zero-stage reports, open allocations
and reallocations, the clearing reconciliation, and a hash. It refuses to close
with unresolved CRITICAL issues.

## Consequences

- Ordinary bookkeeping habits break. Posting a forgotten receipt with last
  month's date is routine in plain ERPNext and refused here once the period is
  closed. This needs to be said in the runbooks (§45
  `opening-stock-migration.md`, `period-close.md`), not discovered by an
  accountant at month end.
- `closed_through_date` in `GSF Settings` (§9.2) becomes a live operational
  control, not a configuration detail. Set too far forward it blocks legitimate
  corrections; left unset it removes the protection entirely — §30.3 warns when
  it is not configured.
- Blocking a Landed Cost Voucher after a cross-company movement means landed
  costs must be applied promptly, before the stock is reallocated. That is an
  operational sequencing requirement on purchasing, not just a system rule.
- Until `GSF Global Revaluation` exists, the honest description of the system is:
  it detects historical divergence and stops, and repair is manual. §35 risk 2
  is mitigated, not eliminated.
- Not covered: what a legitimate correction actually looks like once blocked. The
  manual review path exists (§27.2 `compensate`, §33
  `MANUAL_REVIEW_REQUIRED`) but the accounting recipe for reversing a posted
  reallocation has not been written or tested.
