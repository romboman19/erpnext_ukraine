# ADR 0006 — Ownership conversion, partner return and valuation boundary

## Status

Accepted after Gate 0F spike on 2026-07-13. Durable conversion and partner
return workflows implemented and integration-tested in release 1.0.0 on
2026-07-14.

## Context

Unsold commission or consignment stock can either remain third-party stock,
return to the partner, or become company-owned stock through an explicit
purchase. The conversion must remove zero-valued third-party quantity, create
the own-stock asset and Supplier payable, support contract currency and partial
payments, and preserve exact serial identity.

Inventory Dimension already protects ownership quantity, but Gate 0F also had
to establish whether it creates an independent valuation queue. This matters
when converted own lots with different acquisition costs share one technical
OWN warehouse.

## Evidence

The `postest.local` runner completed and reversed these scenarios:

- Commission UAH: three units received at zero value, two converted at 80 UAH,
  one returned to the partner, one 160 UAH payment and one converted-unit sale.
- Commission USD: two units converted through a 20 USD Purchase Invoice at
  40 UAH/USD and paid with two 10 USD Payment Entries using 410 and 420 UAH.
- Consignment UAH: one unit converted at 70 UAH and paid in full.
- Serialized return: one explicitly selected Serial No returned while the
  other remained in the commission warehouse.

The three Purchase Invoices added 1,030 UAH of stock value. Two Sales Invoices
posted 160 UAH of COGS, leaving 870 UAH of aggregate stock asset. Supplier
outstanding became zero in all scenarios. The foreign-currency payment paths
produced net exchange losses of 10 and 20 UAH after combining Payment Entry GL
with ERPNext's system Exchange Gain/Loss Journal Entries.

The mixed-own-stock probe selected the 400 UAH USD-conversion ownership lot,
but its outgoing SLE consumed 80 UAH from the oldest OWN warehouse FIFO layer.
ERPNext keys its ordinary valuation state by Item and Warehouse; Inventory
Dimension is applied separately for dimension-level negative-stock validation.

## Decision

- Ownership conversion is an explicit, authorized application event. An
  ordinary Material Transfer must never change ownership.
- Conversion removes the exact third-party quantity through a zero-valued
  Material Issue and receives company-owned quantity through a standard
  Purchase Invoice with `update_stock=1`. The Purchase Invoice is the source
  of the stock asset, Supplier payable, obligation currency and provisional
  conversion rate.
- Partner return uses only a zero-valued Material Issue with the original
  ownership coordinates and exact Batch/Serial selection. It creates no
  purchase asset or payable.
- The application event owns idempotency, document links and reverse-order
  cancellation. It locks and validates unreserved quantity before the first
  stock write. Standard ERPNext dependencies prevent cancellation after the
  converted stock is sold or the Purchase Invoice is paid; operators must
  reverse those dependent documents first. Captured or unknown external
  payments require reconciliation rather than blind compensation.
- Foreign-currency payments use an application Payment Entry builder with an
  explicit obligation amount and bank amount. Exchange result is reconciled
  across both the Payment Entry and any system-generated Exchange Gain/Loss
  Journal Entry; neither document is interpreted in isolation.
- Inventory Dimension remains the quantity and audit identity, not a separate
  valuation layer. After conversion to OWN, untracked fungible stock follows
  standard ERPNext warehouse-level valuation. Reports must not present
  dimension-level COGS as if ERPNext calculated it natively.
- When exact per-unit cost identity is legally or operationally required, the
  production workflow must use an ERPNext-supported valuation identity such as
  Serial/Batch valuation and prove it with an integration test. Creating a
  parallel stock ledger is rejected.
- Serialized returns select every Serial No explicitly and validate its
  ownership mapping before submit. Serialized conversion performs an audited
  source-to-target ownership mapping transition before own-stock inward posting
  and restores it on cancellation.

## Consequences

- Aggregate Stock Asset, COGS and payable reconcile through standard ERPNext
  ledgers.
- Ownership-lot reports can state quantity, source, contract and conversion
  cost, but ordinary untracked outgoing COGS is warehouse FIFO.
- Release 1.0.0 provides durable `CC Ownership Conversion` and `CC Partner
  Return` events, generated-document links, authorization, exact Serial/Batch
  mapping, partial quantity, idempotent API and guarded reversal.
