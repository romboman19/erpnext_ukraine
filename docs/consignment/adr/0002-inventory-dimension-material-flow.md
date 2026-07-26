# ADR 0002 — Inventory Dimension material flow and balances

## Status

Provisional — accepted for further Stage 0 spikes, not yet approved for Stage 1.

## Context

The application needs a durable ownership-lot reference on stock movements
without modifying ERPNext core or creating a parallel stock ledger. The main
risk is whether ERPNext Inventory Dimension can both propagate the ownership
value and prevent a sale from consuming another owner's stock.

## Evidence

The `postest.local` Gate 0B material-flow runner confirmed that:

- inward and outward Stock Entry row fields propagate to Stock Ledger Entry;
- Sales Invoice with `update_stock=1` propagates the dimension to Stock Ledger
  Entry;
- ERPNext rejects dimension-level negative stock even when total warehouse
  stock is sufficient;
- zero-valued receipt and issue rows retain zero valuation and zero stock value
  difference;
- a zero-valued dimensioned Sales Invoice creates revenue and receivable GL but
  no non-zero stock asset or COGS entries;
- cancellation restores the active dimension balance to zero.

It also confirmed that ERPNext's `get_stock_balance` does not calculate a
dimension-level balance. With dimension stock `6` and total warehouse stock
`106`, it returned `106` because `qty_after_transaction` is warehouse-wide.

The Serial/Batch spike produced a second native limitation: ERPNext accepted a
Batch and a Serial No received for owner A when the outward Stock Entry and SLE
declared owner B. Bundle and Bundle Entry do not receive Inventory Dimension
fields, so native validation cannot compare those ownership values.

The transaction-variants spike confirmed source/target dimension propagation
for Material Transfer and original-dimension propagation for Sales Invoice
Return. It also confirmed that standard Stock Reconciliation permits an
Inventory Dimension only for opening entries and rejects modification of
existing dimensioned stock.

## Decision

Continue using ERPNext Inventory Dimension as the ownership-lot field carried
by inventory transaction rows and Stock Ledger Entry. Keep native
`validate_negative_stock` enabled for that dimension.

Do not use `get_stock_balance(..., inventory_dimensions_dict=...)` as the
application's ownership balance. Introduce one application service for
dimension balances and allocations. Its first correct implementation may
aggregate active SLE `actual_qty`; the final indexed/summary strategy is gated
by the Stage 0 performance spike.

Add an immutable ownership-lot Link to Batch and Serial No. Before any
third-party outward transaction is submitted, require an explicit Batch/Serial
selection and validate every selected master against the Inventory Dimension
on its transaction row. Native ERPNext validation is insufficient for this
invariant.

Treat standard Stock Reconciliation as an opening-balance tool only for
third-party dimensions. Operational audits and revisions must calculate the
variance in an application document and post controlled, auditable Stock Entry
adjustments with the correct ownership dimension.

Create an idempotent composite SLE index for exact ownership balance lookups:
`(item_code, warehouse, ownership_lot, is_cancelled)`. A 250,000-row temporary
InnoDB benchmark reduced the optimizer estimate from 21,072 rows to 1 and
median latency from 17.1121 ms to 0.3208 ms compared with a single owner index.

No production hooks or Sales Invoice allocation logic may depend on this ADR
until the remaining Gate 0B scenarios pass.

## Consequences

- ERPNext remains the only stock ledger.
- Transaction submission can reuse native dimension-level negative validation.
- Ownership availability, FIFO allocation and reports require an
  application-owned query boundary.
- Balance-query performance and backdated/cancellation behavior require
  explicit regression coverage.
- Bundle and Bundle Entry remain standard ERPNext DocTypes without duplicated
  owner fields; ownership is mapped on Batch/Serial masters and validated at
  the transaction boundary.
- Silent auto-pick cannot be allowed for third-party tracked stock until the
  allocation service has selected and validated exact Batch/Serial values.
- Ordinary transfers and returns must preserve ownership; ownership conversion
  is a separate authorized business event.
- Inventory audit UX cannot rely on standard Stock Reconciliation for existing
  dimension stock.
- The Inventory Dimension single-column search index is not sufficient for
  ownership availability queries; the application owns the composite index
  migration and its regression benchmark.

## Fallback

Retain the Inventory Dimension on SLE, use the proven controlled ownership-lot
mapping for Batch/Serial, and add an indexed balance projection only if the
performance spike requires it. Neither mapping nor projection may become a
second source of truth for stock quantity.
