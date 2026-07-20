# Stage 1 foundation slice 1 evidence

- Date: 2026-07-13
- Sites: `postest-restore.local`, `postest.local`
- Company fixture: `POS Test Ukraine`
- Rehearsal run: `1E520599D2`
- Primary test run: `DE17393598`
- Result: `PASS`

## Migration

The new app metadata was loaded from an isolated temporary source copy. A full
`bench --site postest-restore.local migrate` completed on the paused restore
site, including model sync, fixtures, Workspace sync and `after_migrate` hooks.

The migration created five application-owned DocTypes:

- `CC Settings`;
- `CC Location`;
- `CC Partner Profile`;
- `CC Contract`;
- `CC Account Mapping`.

It also synchronized the `Commission Trade` Workspace and the Manager, User and
Auditor roles.

## Integration smoke

The allow-listed runner created a Company legal-entity Location over the three
Gate 0C technical warehouses, an isolated Supplier and Partner Profile, an
Account Mapping and an Active commission Contract. A second overlapping Active
Contract for the same partner/location/model was rejected.

The runner deleted every created document in reverse dependency order.
`cleanup_errors` was empty and every `remaining_documents` flag was `false`.

## Readiness

Readiness returned:

- stage: `1`;
- status: `ready_for_foundation_configuration`;
- blocking checks: `[]`;
- all five foundation DocTypes, three roles and the Workspace present.

## Primary test rollout

Commit `8eea354` was pushed to the existing draft PR and fast-forwarded into
the clean server checkout. Ownership of the checkout was restored to the
`romboman19` service user before the application was loaded by the container.

`bench --site postest.local migrate` completed. The server-mounted application
code then ran foundation smoke `DE17393598` without a temporary `PYTHONPATH`.
It again rejected the overlapping Active Contract, reported no cleanup errors
and left no created foundation or Supplier documents behind.

Final readiness returned stage `1`, status
`ready_for_foundation_configuration` and no blocking checks. `bench doctor`
reported one worker online and no queued jobs. GitHub Actions job
`isolated-python` passed.

Production was not opened, migrated, restarted or written.
