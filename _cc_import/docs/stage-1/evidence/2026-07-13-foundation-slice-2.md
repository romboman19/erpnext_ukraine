# Stage 1 foundation slice 2 evidence

- Date: 2026-07-13
- Sites: clean GitHub Actions site, `postest-restore.local`, `postest.local`
- Company fixture: `POS Test Ukraine`
- Verified source commit: `5244949`
- Result: `PASS`

## Clean-site integration CI

GitHub Actions run `29273653089`, job `86897249788`, created a new Frappe v16
bench with Python 3.14, Node 24, MariaDB 11.8 and Redis 7.4. It installed
ERPNext and this application on a new `integration.local` site, then ran the
application integration category.

The test created an isolated Company chart, warehouses, Supplier, Location,
Partner Profile, Account Mapping, Settings and an Active commission Contract.
It confirmed that an overlapping Active Contract is rejected and explicitly
removed committed ERPNext setup records. Result: one integration test passed
in 4.493 seconds and a JUnit XML artifact was uploaded. The complete
`clean-site` job passed in 3 minutes 32 seconds.

GitHub Actions run `29273652954`, job `86897249696`, also passed the isolated
lint, compile and 50-unit-test suite in 12 seconds.

## Restore-site regression

The same Frappe integration module ran against the paused
`postest-restore.local` site using the current source and passed. Follow-up
counts for `_CC Integration Company`, `_CC Integration Supplier` and its CC
Contracts were zero, confirming explicit cleanup even though ERPNext Company
setup performs internal commits.

## Test-site bootstrap

The guarded bootstrap accepted only `postest.local`, the exact confirmation
token and Company `POS Test Ukraine`. Its first run created:

- Supplier `CC Test Partner Supplier UAH`;
- Location `CC Test Main Location`;
- Partner Profile `CC Test Partner UAH`;
- Account Mapping `POS Test Ukraine`;
- Draft Contracts `CC-CON-2026-00003` (commission) and
  `CC-CON-2026-00004` (consignment);
- singleton `CC Settings`.

The second run returned `created: []` and all seven records in `existing`.
After the verified source was fast-forwarded into the server checkout, the
bootstrap was run again without a temporary `PYTHONPATH` and returned the same
idempotent result. `CC Settings.enabled` remained `false`; no transaction hook
was activated.

## Server rollout and readiness

The clean server checkout was fast-forwarded from `d273195` to `5244949` as
service user `romboman19`. Checkout ownership was restored to
`romboman19:romboman19`, and the checkout remained clean.

`bench --site postest.local migrate` completed, including application model,
fixture, Workspace and dashboard synchronization. Final readiness returned
stage `1`, status `ready_for_foundation_configuration` and no blocking checks.
`bench doctor` reported one worker online and no queued jobs; the restore-site
scheduler remained intentionally paused.

Production was not opened, migrated, restarted or written.
