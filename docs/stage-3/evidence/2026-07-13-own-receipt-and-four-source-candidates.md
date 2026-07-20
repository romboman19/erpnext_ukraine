# Stage 3 OWN receipt and classified candidate evidence — 2026-07-13

- Site: `postest-restore.local`
- Source: temporary test-container copy of `agent/stage-0-scaffold`
- Production: not opened, migrated, restarted or written
- Migration: PASS
- Full app suite: 5 Frappe integration tests + 79 unit tests, PASS

## BUYOUT and DEFERRED_PURCHASE

The integration test submitted two controlled OWN receipts for the same Item
and technical warehouse:

| Method | Quantity × rate | Purchase Invoice outstanding | Due rule |
|---|---:|---:|---|
| `BUYOUT` | `2 × 40` | `80` | receipt date |
| `DEFERRED_PURCHASE` | `1 × 50` | `50` | explicit receipt date + 30 days |

Each Purchase Invoice used `update_stock=1`. Its Item and resulting Stock
Ledger Entry carried the matching `CC Stock Lot`. Active dimension balances
were `2` and `1`.

A quantity-3 preview returned the BUYOUT lot first and the later deferred lot
second. The output preserved both source methods; payment timing did not alter
the FIFO order.

## Classification guard

The test then posted one native Material Receipt into the same OWN technical
warehouse without a stock-lot dimension. Candidate loading rejected the Item
with `unclassified stock` instead of returning a partial candidate list. After
the native receipt was cancelled, the classified preview was available again.

## Debt and cancellation

ERPNext Purchase Invoice outstanding amounts were the exact `80` and `50`
obligations. Direct cancellation of the linked Purchase Invoice was rejected.
Cancelling each `CC Own Receipt` cascaded through its Purchase Invoice, marked
the lot cancelled and returned its active dimension balance to zero.

## Batch and Serial

A second integration scenario received one Batch-tracked Item and two explicit
Serial Nos through a stock-updating Purchase Invoice. ERPNext created the
physical identities, the application bound them to the immutable OWN lot, and
exact Batch/Serial preview selected the corresponding lot. Controlled
cancellation returned both active lot balances to zero while preserving audit
identity.

## Commands

```bash
bench --site postest-restore.local migrate
bench --site postest-restore.local run-tests \
  --app erpnext_consignment_and_commission
```

The test contour scheduler remained paused throughout the run.
