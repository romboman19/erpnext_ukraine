# ADR 0015 — POS-UA owns the customer flow, GSF owns stock identity

## Status

Accepted on 2026-07-31 for the POS-UA integration of managed GSF sales and
returns.

## Context

POS-UA already owns the cashier session, payment terminal, the unique receipt
lookup barcode, `POS Order`, PRRO fiscalization and refund limits. GSF already
owns global FIFO allocation, exact-value cross-company preparation, technical
Sales Invoice rows and stock-layer movements. Before this decision the two
flows were adjacent but disconnected: POS-UA posted a normal Sales Invoice from
the desk warehouse, while GSF's checkout and return services were callable only
outside POS-UA.

That split loses the evidence required by a return. A visible POS row may be
fulfilled by several FIFO slices belonging to different FOP companies. The
receipt barcode identifies the sale, but only the submitted technical Sales
Invoice rows identify the exact slices and COGS that have to come back.

## Decision

**POS-UA remains the customer-facing saga.** It continues to own payment,
receipt printing, the lookup barcode and PRRO retry/recovery. When a POS Cash
Desk warehouse is an enabled `GSF_OWN_POOL`, its stock leg is delegated to a
`GSF Checkout`; other desks keep the existing standard POS path.

**The GSF checkout stores the POS identities it needs to resume.** Its external
document is the `POS Order`, and every checkout line stores the originating
`POS Order Item.name`. The allocation copies that value to `external_row_id`.
This is the stable bridge from one visible row to all technical FIFO slices.

**The GSF Sales Invoice is still a POS invoice.** GSF supplies the stage
warehouse, layer, allocation and slice fields; the POS adapter supplies payment,
discount, UOM, barcode and POS document fields. Fiscal rendering groups rows
only by `gsf_display_group`, after verifying that legally relevant values match.

**A receipt barcode resolves the original POS Order, never a stock layer
directly.** The return planner follows:

```text
lookup barcode
→ original POS Order Item
→ GSF Allocation.external_row_id
→ GSF Allocation Slice
→ original Sales Invoice Item
→ original sale SLE and COGS
```

For an untracked partial return, slices are consumed in the original technical
Sales Invoice row order. This is deterministic audit policy, not a claim that
an indistinguishable physical unit can be identified without Serial or Batch
tracking.

**Every non-tracked returned technical row creates a new layer.** The layer is
owned by the original seller, dated at the return, and links to both the
immediate sold layer and the root layer of the lineage. The return Sales Invoice
row links to the exact original Sales Invoice row and retains the original
allocation and slice.

**Return value is an invariant, not a report.** Before fiscalization, the
submitted return SLE value must equal the proportional COGS of the referenced
original sale SLE within the configured currency tolerance. A mismatch raises
`RETURN_COGS_MISMATCH` and rolls the stock/accounting transaction back.

**Returns are serialized at the original sale.** The service locks the original
Sales Invoice, recomputes already submitted return quantities, and rejects an
over-return. Direct Sales Invoice returns or cancellations that touch GSF
warehouses are refused unless the managed service flags and row links are
complete.

**No separate return-ledger DocType is introduced.** The POS return order is the
request record; the submitted return Sales Invoice Item is the immutable slice
result; `GSF Stock Layer` and `GSF Layer Movement` are the inventory audit. A
second mutable copy of those relationships would create a reconciliation
problem without adding evidence.

## Consequences

- A GSF-enabled POS sale can use stock received by another FOP without changing
  the cashier flow.
- A return is always booked by the seller from the original receipt, while its
  cost lineage still reaches the FOP and receipt that introduced the stock.
- POS invoice lines may outnumber visible receipt lines; only the controlled
  fiscal renderer may group them.
- Receipt barcode uniqueness locates the sale, but exact physical identity for
  indistinguishable units remains impossible. Serial and Batch returns stay in
  quarantine until the existing tracked-return policy is extended.
- CC and ordinary POS remain isolated because routing requires an enabled GSF
  warehouse binding rather than a global feature check alone.
