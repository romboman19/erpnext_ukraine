# ADR 0007 — OWN receipt, classified stock and payable boundary

## Status

Accepted after the Stage 3 OWN receipt integration slice on 2026-07-13.

## Context

Global FIFO must distinguish two company-owned acquisition methods,
`BUYOUT` and `DEFERRED_PURCHASE`, from commission and consignment stock. The
method is a commercial source snapshot, while both methods create the same OWN
inventory asset. The implementation must not replace ERPNext Stock Ledger,
valuation, General Ledger or Payment Ledger.

Reading ordinary OWN warehouse balance without a durable acquisition identity
would lose the source method and receipt timestamp. Silently merging such
unclassified balance into allocation would make FIFO and debt reports
unverifiable.

## Decision

- `CC Own Receipt` is the controlled application document for `BUYOUT` and
  `DEFERRED_PURCHASE` stock.
- Submit creates one standard `Purchase Invoice` with `update_stock=1`. The
  invoice is the authoritative stock-asset, Supplier payable, obligation
  currency, conversion rate and due-date document.
- A buyout is due on its receipt date. A deferred purchase requires an explicit
  due date after the receipt date. Payment timing never changes sale FIFO
  priority.
- Every receipt row creates one immutable `CC Stock Lot` with relationship
  model `OWN`, its source method, receipt timestamp, source row and linked
  Purchase Invoice row. The Inventory Dimension is written on the Purchase
  Invoice Item and resulting Stock Ledger Entry.
- Existing Batch and Serial behavior remains ERPNext-native. After submit, the
  physical identity receives the immutable stock-lot owner and exact preview
  verifies it against the active warehouse balance.
- Direct cancellation of a linked Purchase Invoice is forbidden. Cancellation
  starts from `CC Own Receipt` and cascades to the invoice; ERPNext dependency
  checks remain authoritative for downstream stock and accounting documents.
- Candidate loading is fail-closed. Any non-zero SLE balance without a
  `CC Stock Lot` in an allowed CC technical warehouse raises a controlled
  classification error. A future rollout must classify or relocate legacy
  stock before live global FIFO is enabled.
- Untracked OWN valuation remains standard warehouse-level ERPNext FIFO. The
  application reports source-lot quantity and commercial origin, but does not
  claim a parallel lot-level valuation queue.

## Consequences

- BUYOUT and DEFERRED_PURCHASE now participate in the same deterministic FIFO
  candidate stream as COMMISSION and CONSIGNMENT.
- Outstanding own-purchase debt is read from standard Purchase Invoice and
  Payment Ledger, avoiding duplicate debt state.
- Native stock posted directly into a CC technical warehouse cannot be sold by
  the future live allocator until it is explicitly classified.
- Stage 3 can proceed to atomic reservations and Sales/POS posting without a
  partial OWN adapter.
