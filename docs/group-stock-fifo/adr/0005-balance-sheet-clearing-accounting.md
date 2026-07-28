# ADR 0005 — Balance-sheet clearing accounting

## Status

Accepted on 2026-07-27, after gates 0a, 0b and 0j. Numbered per
[spec](../spec-v1.0.md) §40.

## Context

§15.2 posts the reallocation against internal clearing accounts and §4.3
forbids those postings from reaching P&L. §15.3 warns against N² account pairs
and prescribes a specific shape: one `Internal Stock Due From` and one
`Internal Stock Due To` per company, plus a mandatory
`Counterparty Accounting Company` accounting dimension.

§15.3 also demands proof that ERPNext even permits a Stock Entry to post its
difference against a balance-sheet account, with a compensating Journal Entry as
the documented fallback if it does not.

## Evidence

Gate 0b ([evidence](../spikes/evidence/2026-07-27-gate-0b-exact-value-transfer.md))
settled the platform question: no fallback is needed. ERPNext's
`validate_difference_account` rejects only a `Stock`-type difference account. An
Asset account passes, and the resulting GL was:

| Voucher | Account | Debit | Credit |
|---|---|---:|---:|
| issue | clearing (source) | 2000.00 | |
| issue | Stock In Hand (source) | | 2000.00 |
| receipt | clearing (target) | | 2000.00 |
| receipt | Stock In Hand (target) | 2000.00 | |

The P&L effect of each voucher was **exactly zero**, which closes gate 0a with
the same run and satisfies §37.23.

Gate 0j ([evidence](../spikes/evidence/2026-07-27-gate-0j-end-to-end.md)) left
the clearing accounts carrying a real inter-company position — the selling
company at −6500 after receiving layers worth 6500. That is the intended
behaviour of the scheme, not a defect.

## Decision

**Reallocation posts against per-company balance-sheet accounts, root type
Asset. No compensating Journal Entry is required.** A regression test asserts
`sum(P&L GL entries) = 0` for every reallocation voucher, per §37.23.

**Two accounts per company, not one.** The spikes used a single
`GSF Group Clearing` for convenience. Production follows §15.3: an
`Internal Stock Due From` account for the source side and an
`Internal Stock Due To` account for the destination side, both configured on
`GSF Group Member` (§9.4) and mirrored onto
`GSF Location Company Binding` (§6.3).

**A `Counterparty Accounting Company` accounting dimension is mandatory on
every clearing posting.** Without it the balances aggregate into a single
opaque figure and §37.23's "reconcile by counterparty" cannot be evaluated.

**Never an expense account.** §44 forbids a P&L expense account as a permanent
clearing workaround, and §15.3 repeats it. This is a hard rule, not a
preference.

## On the accumulating balance

The clearing accounts accumulate a growing inter-company position — they do not
discharge themselves. This is expected: §15.2 states the balances are
eliminated at group reporting level, and §15.3 plus §37.23 describe
**reconciliation by counterparty**, not settlement by payment.

So the question the earlier draft of this ADR left open is answered by the spec
itself. What GSF owes is a reconciliation report
(`GSF Financial Integrity`, §31.6: "due-from = due-to за
group/counterparty/reallocation") and a period-close gate (§25.4). Whether the
owner additionally wants real money to move between FOPs is a business decision
outside this ADR and outside the domain.

## Consequences

- `GSF Group Member` and `GSF Location Company Binding` both need the two
  account links before the first reallocation can post; readiness (§30.2) must
  block activation when they are missing, raising `CLEARING_ACCOUNT_MISSING`.
- The counterparty dimension has to exist on the site before GSF is enabled.
  That is an `erpnext_ua` setup dependency, not a GSF-only one.
- Nightly clearing reconciliation (§30.5) and the `CLEARING_IMBALANCE` code
  (§33) become meaningful only once the two-account shape is in place; with a
  single account the imbalance is undetectable by construction.
- The spike fixture keeps its single-account shape. It is evidence of the
  posting mechanism, not a model of the production chart of accounts, and the
  evidence files say so.
