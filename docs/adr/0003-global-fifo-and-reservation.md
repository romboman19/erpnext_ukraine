# ADR 0003 — Global FIFO and atomic reservation

## Status

Accepted and implemented through the persistent Stage 3 reservation boundary.

## Context

One Item may be available as own, commission and consignment stock in three
technical warehouses at the same physical location. ERPNext valuation FIFO is
warehouse-local and therefore cannot choose the globally oldest business lot
before Sales Invoice creation.

Business source method is more detailed than ownership: immediate buyout and
deferred purchase are both company-owned `OWN` stock, while commission and
consignment retain their matching third-party ownership models. Payment timing
must not create a source-method priority ahead of physical receipt time.

Two POS checkouts may also resolve the same final unit. Candidate selection
without a database-level reservation would allow both checkouts to proceed
before ERPNext negative-stock validation is reached.

## Evidence

The Gate 0C runner created three leaf warehouses and received the same Item as:

| Model | Receipt time | Qty | Valuation rate |
|---|---:|---:|---:|
| Commission | 08:00 | 2 | 0 |
| Own | 09:00 | 2 | 50 |
| Consignment | 10:00 | 2 | 0 |

The application resolver allocated a five-unit request as Commission `2`, Own
`2`, Consignment `1`. A split Sales Invoice produced stock value/COGS only for
the Own row. After cancellation, a second allocation selected Commission again
and submitted successfully.

Two independent bench processes then attempted to reserve one available unit
with the same conditional SQL update. Exactly one affected a row; the other
received a controlled zero-row result. The final reserved quantity was one,
not two.

## Decision

Use an application-owned `AllocationService` before Sales Invoice creation.
Every candidate and allocation slice carries an immutable source snapshot:

| Source method | Ownership model |
|---|---|
| `BUYOUT` | `OWN` |
| `DEFERRED_PURCHASE` | `OWN` |
| `COMMISSION` | `COMMISSION` |
| `CONSIGNMENT` | `CONSIGNMENT` |

Its deterministic priority is:

1. exact scanned/selected Serial No;
2. required Batch selection;
3. global FIFO across every allowed technical warehouse by
   `(receipt_datetime, receipt_name, receipt_row_index, lot_name)`.

Source method, payment due date and commercial priority do not alter this
ordering. Any permissioned override is a separate audited action.

Candidate filtering must happen before sorting and must include location,
allowed warehouses, Item/UOM, positive unreserved quantity, lot/contract
status, incidents/blocks, pending transfer, serial/batch constraints and fiscal
route.

Split one logical requested quantity into one or more allocation slices. Each
slice becomes a Sales Invoice Item row with the exact warehouse, ownership lot
and tracking values. ERPNext remains responsible for SLE, valuation and GL
after the source has been selected.

Reserve selected quantities atomically in the database. The persistence model
must support a conditional update equivalent to:

```sql
UPDATE stock_lot
SET reserved_qty = reserved_qty + :qty
WHERE name = :lot
  AND available_qty - reserved_qty >= :qty;
```

Zero affected rows is a controlled concurrent-stock conflict. Reservation
records require a unique idempotency key tied to POS Order and allocation
sequence. Cancel, timeout and hold expiry release them atomically.

The production implementation persists a server-owned `CC Allocation` and its
immutable `CC Allocation Slice` rows. It locks lots in sorted name order,
reconciles the active Stock Ledger balance under `FOR UPDATE`, conditionally
increments `reserved_qty`, and separately locks exact active Serial identities.
The allocation name is a deterministic hash of the idempotency key and request
fingerprint, so an identical concurrent request cannot consume a naming-series
row or create a second business reservation.

`reserve_stock` is a dedicated transaction boundary. It must be the first write
operation of a hold request. This permits a full transaction retry after the
MariaDB deadlock/duplicate-insert resolution that can occur when two processes
submit the same idempotency key. Callers must commit the successful hold before
performing unrelated writes. A replay with the same key and fingerprint returns
the existing allocation; reuse of the key for another payload fails closed.

## Consequences

- Standard warehouse FIFO is not the business allocation source.
- Cashiers do not choose technical warehouses or lots.
- Own-stock candidates may come from a dedicated adapter, but must expose the
  same candidate contract and stable FIFO key as third-party lots.
- Manager override is a separate permissioned action with reason and immutable
  audit evidence.
- Ordinary allocation never changes ownership while moving stock.
- A cancelled Sales Invoice returns its slice to the candidate pool; future
  allocation must remain deterministic.
- Reservation acquisition order must be deterministic for multi-row carts to
  avoid deadlocks.
- A reservation cannot be marked consumed until its consumer DocType and
  document exist; release, expiry and consumption are terminal audit states.

## Rejected alternatives

- Relying on ERPNext FIFO independently inside each Warehouse: it cannot choose
  globally between Own, Commission and Consignment.
- Checking quantity and reserving in separate transactions: two checkouts can
  both observe the final unit.
- Allowing silent POS auto-pick after preview: preview and submit could consume
  different owners and fiscal routes.
