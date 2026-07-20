# ADR 0005 — POS split saga boundary

## Status

Accepted after Gate 0E spike on 2026-07-13.

## Context

The installed `erpnext_ua` app provides a real POS Order with cart items,
payments, lookup token, idempotency key, return workflow and a checkout state
machine. Its accounting document remains standard Sales Invoice.

The current schema has one `fiscal_mode` and one `sales_invoice` link. A mixed
checkout can require several invoices because it is grouped by Company, legal
entity and fiscal/non-fiscal policy. Storing one of those invoices in the
existing field would lose the other routes and make retry/compensation unsafe.

Gate 0E also found that the current POS Order JSON uses `title_field="name"`.
Frappe v16 does not consider that a valid metadata field when validating a new
parent Custom Field. The domain app must not patch this other app merely to
store saga state.

## Decision

- `erpnext_ua` POS Order remains the logical checkout root and owns cashier UX,
  payment capture, `lookup_token` and its existing `idem_key`.
- This app owns an external 1:N route model. Each route is uniquely identified
  by `POS Order × Company × legal entity × fiscal route` and links exactly one
  Sales Invoice.
- No parent custom fields are required on POS Order. The adapter returns and
  registers the route collection through a versioned contract.
- Sales Invoice and Sales Invoice Item receive server-owned snapshot fields for
  POS Order, split group, legal entity, fiscal route, relationship model, lot
  and idempotency key.
- In fiscal checkout, own and consignment routes are fiscal; commission routes
  are non-fiscal unless the snapshotted policy explicitly changes. In an
  allowed non-fiscal checkout, relationship models may share a route when
  Company and legal entity match.
- One logical payment plan is deterministically allocated across route totals.
  The sum of allocations per SI must equal that SI total, and the sum across SI
  must equal the POS Order total.
- A route is created before its SI. Retry reuses an existing route/SI and only
  executes missing steps. Stable keys are mandatory for SI, allocations,
  fiscal registration and print jobs.
- Fiscal and non-fiscal print jobs are separate persistent jobs. Network calls
  occur after commit and use their job keys for provider idempotency.
- If checkout cannot complete, submitted SI are cancelled in reverse order,
  drafts are removed, and all unconsumed reservations are released. Captured or
  unknown external payments require manual/reconciliation state instead of
  blind compensation.
- Return resolution starts with the POS lookup token, but every returned row is
  matched to its original allocation. The return SI restores the exact lot,
  warehouse, serial/batch and relationship snapshot.

## Consequences

- Stage 1/3 need production equivalents of POS Saga Route, Reservation and
  Print Job; the `TP Spike` DocTypes are evidence only.
- The POS adapter must support both the current `erpnext_ua` implementation and
  a generic Sales Invoice fallback without importing private POS services.
- Multiple FOP/Company fixtures are required before rollout. Gate 0E used two
  legal-entity routing keys on one test Company because the site currently has
  no FOP Profile records.
- Real PRRO network behavior remains the fiscal adapter's integration test. The
  test cash desk has no PRRO Cash Register, so Gate 0E verified route and print
  idempotency without external fiscal calls.
