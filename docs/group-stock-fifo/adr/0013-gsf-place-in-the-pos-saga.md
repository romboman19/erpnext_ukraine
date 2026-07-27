# ADR 0013 — GSF place in the POS saga

## Status

Proposed on 2026-07-27. Blocks Phase 1: no GSF production code is written
before this is accepted.

The number is inherited from the base spec, which already refers to
"ADR-013". GSF keeps its own ADR sequence; commission ADRs 0001–0009 live in
[`docs/consignment/adr`](../../consignment/adr).

## Context

Base spec §9.16 and §23 design `GSF Checkout` as a self-contained saga with 18
states, its own `payment_state`, `fiscal_state`, `prro_receipt_id`,
`retry_count` and its own compensation path.

The app already runs two sagas over one physical receipt:

| Saga | Owns | States |
| --- | --- | --- |
| `POS Order` | payment capture, PRRO, print, shift, cash | 18, `Building` … `Manual Review` |
| `CC POS Checkout` | commission route of one receipt | `PLANNED → IN_PROGRESS → COMPLETED / COMPENSATING / MANUAL_REVIEW` |

`CC POS Checkout` is already subordinate: its `external_order_doctype` is
`POS Order`. [ADR 0005](../../consignment/adr/0005-pos-split-saga-boundary.md)
fixed that one-level model — one top-level receipt, several domain routes under
it, each holding exactly one Sales Invoice.

A third top-level saga would make `GSF Checkout` a second claimant on the
fiscal state of one physical receipt. Two state machines both believing they
own PRRO retry produce a duplicated or missing fiscal receipt — the base spec
names this itself in §35, risk 6.

## Decision

`GSF Checkout` is a **route under `POS Order`**, shaped like `CC POS Route`.

**It owns:** reservation with TTL, stock preparation on the Sale Stage
warehouse, layer reallocation between companies, and its own Sales Invoice.

**It does not own:** payment capture, fiscalization, printing, shift, or
payment compensation. Those stay in `POS Order`.

Concretely:

- `payment_state`, `fiscal_state`, `prro_receipt_id` and `retry_count` are
  removed from the §9.16 schema.
- The §23.1 state machine reduces to its stock part:
  `DRAFT → RESERVING → RESERVED → PREPARING_STOCK → STOCK_PREPARED →
  ERP_SALE_SUBMITTED → CONSUMED`, plus `EXPIRED`, `CANCELLED`, `COMPENSATING`,
  `COMPENSATED`, `FAILED`, `MANUAL_REVIEW`. Thirteen states instead of
  eighteen; every removed state was fiscal or payment.
- The §14.1 sequence stays valid, but `POS Order` initiates fiscalization.
- Route identity follows ADR 0005: `POS Order × Company × physical location ×
  fiscal route`, one Sales Invoice per route.
- The route row is created before its Sales Invoice. Retry reuses the existing
  route and invoice and executes only the missing steps. Stable keys are
  mandatory for the invoice, the allocations and the reallocation documents.
- One logical payment plan is allocated deterministically across route totals
  across **all** domains present in the order. The sum per invoice equals that
  invoice total; the sum across invoices equals the `POS Order` total.
- On failure GSF releases unconsumed reservations and reverses its own stock
  documents in reverse order. Captured or unknown external payments are never
  compensated by GSF — they raise manual review on `POS Order`.
- In `Sales Invoice.on_submit` the GSF consume handler runs next to the
  commission one, before `ua_fiscal.sales_invoice.on_submit`. Anything that can
  fail happens before the fiscal receipt exists.
- GSF adds no print queue. It uses the queue `POS Order` owns.

**Out of scope here:** who decides which FOP sells which line. `GSF Checkout`
receives an already-decided route list, exactly as
`validate_pos_checkout_request` does today. Routing rules are a separate domain
and a separate ADR.

## Alternatives considered

**`GSF Checkout` as an independent saga** (base spec). Rejected: dual ownership
of the fiscal state, described above.

**Extend `CC POS Checkout` to carry GSF routes.** Rejected: it couples two
stock domains into one saga row and breaks the ownership rule that GSF never
writes `cc_*` fields.

**Move reallocation out of checkout into a nightly batch.** Rejected: cost has
to transfer at sale time to keep §16 exact, and a backdated batch collides with
the §25 fail-closed rule on closed periods.

## Consequences

- Fiscal recovery keeps a single owner, `ua_fiscal.recovery`. GSF inherits
  retry and offline behaviour instead of reimplementing it.
- `POS Order` needs a domain-neutral way to enumerate routes from more than one
  stock domain. The `StockDomainProvider` protocol becomes necessary at the
  first mixed CC + GSF checkout — not earlier, and not as a versioned
  cross-repository API. The shape follows
  [`contracts/pos-v1.md`](../../consignment/contracts/pos-v1.md).
- A mixed checkout puts two route collections under one order. Payment
  allocation must be computed once across both collections; ADR 0005 assumed a
  single domain and needs this amendment recorded.
- Consolidating `POS Print Job` and `CC POS Print Job` into one queue becomes a
  prerequisite of Phase 1, tracked as separate work.
- Base spec §9.16 and §23.1 are amended by this ADR; §14.1 stays with a changed
  initiator. Once the base spec lands in the repo, both sections must carry the
  amendment inline.
- The reduced state machine has no fiscal states, so a GSF route can complete
  while the receipt is not yet fiscalized. Monitoring must read fiscal progress
  from `POS Order`, never from `GSF Checkout`.
