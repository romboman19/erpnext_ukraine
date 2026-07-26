# Gate 0B Serial/Batch evidence — 2026-07-13

## Result

`NATIVE FAIL / FALLBACK PASS`.

ERPNext v16 carries Inventory Dimension and Serial/Batch Bundle references in
the same Stock Ledger Entry, but does not validate that the selected Batch or
Serial No belongs to the Inventory Dimension owner on the transaction row.

A controlled fallback with an owner Link on Batch and Serial No plus an
application-level pre-submit guard rejected both cross-owner probes.

## Isolation

- compose project: `frappe-test`;
- site: `postest.local`;
- company: `POS Test Ukraine`;
- native behavior runner commit: `8f3f963`;
- fallback guard runner commit: `1d3db7d`;
- production compose project `frappe` was not changed or restarted.

The site's `enable_serial_and_batch_no_for_item` setting was initially `0`.
The runner temporarily set it to `1` and restored it to `0` in a nested
`finally` block.

## Native schema finding

After Inventory Dimension creation, `tp_spike_lot` was absent from all four
tracking DocTypes:

| DocType | Native dimension field |
|---|---|
| Serial and Batch Bundle | no |
| Serial and Batch Entry | no |
| Batch | no |
| Serial No | no |

This matches ERPNext source: Inventory Dimension explicitly skips Serial and
Batch Bundle and Serial and Batch Entry when creating custom fields.

## Native mismatch probes

Two owner values were used:

- owner A: `TP-GATE-0B-LOT-001`;
- owner B: `TP-GATE-0B-LOT-002`.

### Batch

Batch `TP-G0B-B-00003` was received with owner A. Batch
`TP-G0B-B-00004` was received with owner B. ERPNext accepted an outward bundle
containing batch `TP-G0B-B-00003` while the Stock Entry row and SLE carried
owner B.

### Serial No

Serial `TP-G0B-S-00003` was received with owner A. Serial
`TP-G0B-S-00004` was received with owner B. ERPNext accepted an outward bundle
containing serial `TP-G0B-S-00003` while the Stock Entry row and SLE carried
owner B.

Both native checks therefore returned `FAIL_ACCEPTED_CROSS_OWNER`.

## Fallback proof

The runner created a read-only Link field `tp_spike_lot` on Batch and Serial No
and stored the owner established by each receipt. It did not add the field to
Bundle or Bundle Entry.

Before submit, the fallback guard compared:

1. the Inventory Dimension value on the Stock Entry row;
2. each explicit Batch/Serial selection, from legacy fields or an existing
   bundle;
3. the owner Link on the corresponding Batch or Serial No master.

The guarded probes were rejected with precise owner-mismatch errors:

- Batch owner A used as owner B: `PASS_REJECTED_CROSS_OWNER`;
- Serial owner A used as owner B: `PASS_REJECTED_CROSS_OWNER`.

The same probes without this guard were accepted by ERPNext, proving that the
fallback is required and is not duplicating native validation.

## Stock Ledger and valuation

All six native submitted Stock Entries had both a
`serial_and_batch_bundle` reference and `tp_spike_lot` in SLE. Every SLE and
bundle entry had zero valuation and zero stock value difference.

The mismatch caused the dimension balance to decrease for owner B while the
physical Batch/Serial belonging to owner A was consumed. This is the exact
inconsistency the application guard must prevent.

## Cleanup

- all six submitted Stock Entries were cancelled in reverse order;
- all four active dimension balances returned to `0`;
- batch owner A warehouse quantity returned to `0`;
- serial owner A warehouse returned to `null`;
- the original Stock Settings value was restored to `0`.

The two test-only Custom Fields and fixture masters remain on `postest.local`
for subsequent transfer, return and Sales Invoice bundle probes.

## Reproduction

```bash
bench --site postest.local execute \
  erpnext_consignment_and_commission.consignment_and_commission.spikes.serial_batch.run_serial_batch_flow \
  --kwargs '{"confirm_site":"postest.local","confirm_write":"RUN_GATE_0B","company":"POS Test Ukraine"}'
```

## Required implementation rules

1. Batch and Serial No must carry an immutable ownership-lot Link established
   by the first accepted third-party receipt.
2. Every third-party outward row must explicitly identify its Batch/Serial; an
   unqualified auto-pick is not permitted.
3. A pre-submit validator must compare all selected tracking masters with the
   row's ownership Inventory Dimension.
4. Returns and transfers must preserve or validate the same mapping.
5. Mapping repair must be an audited manager workflow, not a silent update.

## Open items

1. Run the guard through Sales Invoice `update_stock=1` with bundles.
2. Verify Material Transfer and Return for both Batch and Serial No.
3. Decide the lifecycle and permissions of the production ownership-lot field.
4. Cover both modern bundle selection and legacy Serial/Batch fields in
   integration tests.
