# Evidence — atomic global FIFO reservation

Date: 2026-07-13

Environment: isolated Docker ERPNext/Frappe v16 site `postest-restore.local`.
Production containers and production site were not changed.

## Implemented boundary

- server-owned `CC Allocation` plus immutable slice child rows;
- unique idempotency key bound to a canonical request fingerprint;
- deterministic allocation name and FIFO coordinates;
- sorted `CC Stock Lot` row locks and conditional `reserved_qty` updates;
- live Stock Ledger balance reconciliation while holding each lot lock;
- exact active Serial No reservation lookup and reconciliation;
- release, consumption and TTL expiry terminal transitions;
- hourly expiry job and idempotent lookup indexes;
- fail-fast dedicated-transaction guard before any unrelated write.

## Functional verification

The Frappe integration lifecycle covers:

1. a BUYOUT receipt split across two lots;
2. one all-or-nothing multi-lot reservation in FIFO order;
3. same-key replay without incrementing `reserved_qty` again;
4. same key with a different payload rejected;
5. reserved quantity excluded from a new allocation preview;
6. release returning both lots to the candidate pool;
7. TTL expiry releasing quantity;
8. exact Serial identity conflict and reuse after release;
9. direct audit-document creation rejected;
10. reservation after unrelated writes rejected;
11. consumption against a missing document rejected.

## Concurrency verification

Two independent `bench execute` processes competed for one physical unit.

- Different idempotency keys: one process created a reservation; the other
  received controlled insufficient stock. Final `reserved_qty = 1.0`.
- The same idempotency key and payload: both processes returned
  `CC-ALLOC-32FA2D89FE28E0C9C247`. Final `reserved_qty = 1.0`.

The test-only probe is hard-restricted to `postest-restore.local` and requires
the explicit confirmation token `RUN_RESERVATION_PROBE`. Cleanup reported zero
active allocations and zero lots with a residual reservation.

## Automated checks

- isolated Python unit suite: 84 passing;
- reservation Frappe integration suite: 2 passing;
- module compile check: passing;
- isolated Frappe full application suite after an idempotent migration: 7
  integration tests passing, including all earlier receipt/ownership/cancel
  coverage and the 2 reservation tests;
- JSON parse, full-package compile and `git diff --check`: passing;
- GitHub clean-site and isolated-Python checks are required before this slice
  is merged.
