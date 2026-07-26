# Gate 0E evidence — POS split saga

Date: 2026-07-13
Site: `postest.local`
Company: `POS Test Ukraine`
Result: `PASS WITH REQUIRED EXTERNAL SAGA`

Production was not opened, migrated, restarted or written. The runner used the
existing open test shift and submitted/cancelled standard Sales Invoice and
Stock Entry fixtures. Final balances of all five test lots were zero.

## Installed POS boundary

- `POS Order` exists in module `UA POS` from `erpnext_ua`.
- Test cash desk: `POS Test Desk`.
- Open shift: `POS-SHIFT-2026-00007`.
- The DocType has `lookup_token`, unique `idem_key`, items, payment plan,
  status machine and one `sales_invoice` field.
- It does not have a 1:N generated-document model.
- The test desk has no PRRO Cash Register; no external fiscal call was made.
- Only `Cash` had a Company payment account. The integration run used one cash
  tender; multi-tender waterfall is covered by the pure regression tests.

Attempting to add a parent custom field exposed an upstream metadata constraint:
the current JSON declares `title_field="name"`, which Frappe v16 rejects as an
invalid field during Custom Field validation. Gate 0E therefore stores split
state in its own route DocType and does not patch `erpnext_ua`.

## Reproducible runner

```bash
bench --site postest.local execute \
  erpnext_consignment_and_commission.consignment_and_commission.spikes.pos_saga.run_pos_saga_flow \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B","company":"POS Test Ukraine"}'
```

The runner is test-site allow-listed and is neither imported by hooks nor
exposed as an API.

## Mixed checkout and payment split

Logical order: `POS-ORD-2026-00011`, lookup token
`f914e877-39b6-4f61-9c59-fb6b9f4181af`.

One payment plan contained `Cash 300 UAH`. The service produced three routes:

| Legal entity | Model | Fiscal route | Sales Invoice | Allocated payment |
| --- | --- | --- | --- | ---: |
| TP-LEGAL-ENTITY-A | Own | FISCAL | ACC-SINV-2026-00012 | 100 UAH |
| TP-LEGAL-ENTITY-A | Commission | NON_FISCAL | ACC-SINV-2026-00013 | 100 UAH |
| TP-LEGAL-ENTITY-B | Consignment | FISCAL | ACC-SINV-2026-00014 | 100 UAH |

Every SI was `is_pos=1`, `update_stock=1`, submitted, fully paid and retained
its route, relationship model, legal entity, technical warehouse and exact
Inventory Dimension lot snapshot.

The test site has no FOP Profile rows, so two legal-entity keys were used as
adapter routing snapshots on the same Company. Stage 1 must add real FOP/Company
fixtures before pilot rollout.

## Timeout and retry

The first route and `ACC-SINV-2026-00012` were committed, then the runner raised
a simulated timeout.

First retry:

- reused `ACC-SINV-2026-00012`;
- created only `ACC-SINV-2026-00013` and `ACC-SINV-2026-00014`.

Second retry:

- created no document;
- reused all three SI;
- route count remained three and SI count remained three.

The unique route owns the SI link, so a transport timeout after commit cannot
duplicate the non-fiscal or fiscal part.

## Print jobs

The first pass created three persistent jobs:

- two `FISCAL_RECEIPT` jobs for the own and consignment routes;
- one `NON_FISCAL_GOODS_RECEIPT` job for commission.

The second pass reused all three job keys and created none. This validates the
queue/provider idempotency boundary without calling PRRO or a printer.

## Return by lookup chain

- Return POS Order: `POS-ORD-2026-00012`.
- Original commission SI: `ACC-SINV-2026-00013`.
- Return SI: `ACC-SINV-2026-00015`.
- Lot: `TP-GATE-0E-4FFD47A1F2-COMMISSION`.
- Warehouse: `TP Gate 0C Commission - PTU`.
- Ownership snapshot: `COMMISSION`.

The dimension balance was `0` after sale and `1` after return. Return SLE had
`actual_qty=1`, the same lot and warehouse, zero valuation and zero stock-value
difference.

## Partial checkout compensation

Compensation order `POS-ORD-2026-00013` reserved one own and one commission lot.
The first route submitted `ACC-SINV-2026-00016`; the second route then failed by
scenario.

The compensation plan:

1. cancelled `ACC-SINV-2026-00016` (`docstatus=2`);
2. released both reservations;
3. marked the route compensated and POS Order cancelled.

Both lot balances returned to `1`, proving that compensation restored stock
before receipt cleanup.

## Cleanup

- cancelled return SI `ACC-SINV-2026-00015`;
- cancelled split SI `ACC-SINV-2026-00012`—`00014`;
- the compensation SI was already cancelled;
- cancelled receipt Stock Entry `MAT-STE-2026-00022`—`00026`;
- changed all test reservations to `RELEASED`;
- verified all five lot balances equal zero.

Persistent test-only routes, reservations, print jobs, snapshot fields and
cancelled documents remain as auditable spike evidence.

## Required implementation constraints

1. POS Order is an adapter-owned root, not the store for 1:N route state.
2. Route creation and POS Order row locking precede SI creation.
3. Stable route/SI/print keys are mandatory across retries.
4. Payment allocations must reconcile to both route totals and logical order
   total.
5. Compensation is state-aware and never blindly reverses an unknown external
   payment.
6. Returns resolve the original allocation and restore its exact ownership
   coordinates.

See [ADR 0005](../../adr/0005-pos-split-saga-boundary.md).
