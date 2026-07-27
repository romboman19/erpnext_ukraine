# ADR 0004 — Reallocation accounting and posting order

## Status

Accepted on 2026-07-27, after gates 0a, 0b, 0c and 0j, with one item explicitly
left open pending a tax opinion.

## Context

`MANAGEMENT_REALLOCATION` moves a cost layer from the FOP that owns it to the
FOP that is selling it. §15 requires this to carry no internal margin and to
leave P&L untouched. The mechanism has to be built from documents ERPNext
already understands, without a parallel ledger.

## Evidence

Gate 0b ([evidence](../spikes/evidence/2026-07-27-gate-0b-exact-value-transfer.md))
proved the mechanism works and closed 0a with the same run.

A Material Issue in the source company against a balance-sheet clearing
account, paired with a Material Receipt in the target company against its own
clearing account, transfers value exactly. Two runs: one where the target
warehouse was deliberately valued at 1500 before a layer worth 1000 per unit
arrived, and one where the unit cost was 1000 over three units and does not
divide evenly. Both moved the value with `delta = 0.00`. In the second, ERPNext
released 666.67 rather than the unrounded 666.6667 — but applied the *same*
rounding to both documents, so nothing was lost or duplicated.

ERPNext only forbids a `Stock`-type difference account. An Asset account passes
validation, and the P&L effect of both vouchers was exactly zero, which is what
§15 asks for.

Gate 0c ([evidence](../spikes/evidence/2026-07-27-gate-0c-sale-stage-cogs.md))
established that the receipt rate cannot be taken from GSF's own layer registry
— the inventory dimension does not steer valuation, so only the ledger knows
what actually moved.

Gate 0j ([evidence](../spikes/evidence/2026-07-27-gate-0j-end-to-end.md)) ran
three reallocations and a sale, and left the clearing accounts in this state:

| Account | Debit | Credit | Balance |
|---|---:|---:|---:|
| `GSF Group Clearing - ФКРВ` | 2000.00 | 2000.00 | 0.00 |
| `GSF Group Clearing - ФКВВ` | 3300.00 | 3300.00 | 0.00 |
| `GSF Group Clearing - ФКІВ` | 1200.00 | 7700.00 | **−6500.00** |

The two zero balances are an artifact of the spike, not a result: the fixture
seeds its opening stock through the same clearing account, which happens to
offset the outgoing side. In production that opening stock arrives from a
supplier. The number that matters is the seller's **−6500.00**: after receiving
layers worth 6500 from other FOPs, the selling company carries a 6500 credit
balance and the source companies carry matching debits.

## Decision

**Reallocation is a Material Issue in the source company and a Material Receipt
in the target company, both against a per-company balance-sheet clearing
account** (`GSF Group Clearing`, root type Asset). No internal margin, no
revenue, no expense. A regression test asserts the P&L effect of each voucher is
exactly zero.

**The receipt rate is derived from the issue's actual `stock_value_difference`,
divided by the moved quantity.** It is never read from the layer registry and
never recomputed from the target warehouse's valuation. This follows directly
from gate 0c and is the operative reading of §16.

**A slice already owned by the seller moves by Material Transfer, not through
clearing.** Gate 0j pushed all three slices through the clearing account,
including the one the seller already owned, to keep a single code path in the
spike. It nets to zero and is harmless in a test, but it is the wrong document
for an intra-company move and must not survive into Phase 1.

**Handler order on `Sales Invoice.on_submit`:** the GSF consume handler runs
next to the commission one, before `ua_fiscal.sales_invoice.on_submit`. Anything
that can fail happens before a fiscal receipt exists, per the revision's §29.2
and [ADR-013](0013-gsf-place-in-the-pos-saga.md).

## Open: the clearing account does not clear itself

The name is aspirational. Goods move; money does not. Each reallocation leaves
a receivable in the source FOP and a matching payable in the selling FOP, and
nothing in the current design ever settles them. Left alone, a working group of
FOPs accumulates a monotonically growing inter-party balance — 6500 after a
single six-unit sale in gate 0j.

§15 makes the *income statement* neutral. It does not make the *balance sheet*
neutral, and a growing payable between related parties is exactly what a tax
inspection examines. This ADR therefore does **not** decide the settlement
mechanism. Two things must happen before Phase 1 posts a single reallocation on
a production site:

1. the tax opinion on `MANAGEMENT_REALLOCATION` must cover the accumulated
   inter-FOP balance, not only the zero-margin transfer itself;
2. a settlement process must exist. The commission module already has the
   shape — `CC Settlement Report`, supplier debt journal entries, partial
   `Payment Entry`, multi-currency outstanding — and is the obvious model rather
   than a fresh design.

Until both are settled, the clearing account is a correct bookkeeping device
with an unfinished business process behind it, and that should be stated plainly
rather than discovered during an audit.

## Consequences

- Reallocation needs no revenue documents and no transfer pricing, which is why
  the accounting half of Phase 0 went green so quickly.
- Rounding is delegated entirely to ERPNext. GSF must not pre-round a rate
  before handing it to the receipt; gate 0b shows the platform applies the same
  rounding to both legs only if it is allowed to do the rounding.
- The intra-company Material Transfer path is a second document shape to build
  and test, not a variant of the first.
- Settlement, once designed, will be the largest piece of accounting work in the
  domain, and it is currently outside every estimate made so far.
