# Production readiness report

Assessment date: 2026-07-15
App version: 0.3.0
Branch: `codex/production-hardening`
Baseline commit: `7afdc74`

## Decision

Repository/release readiness: **10/10 — release candidate**.

Deployment readiness for a specific company: **NO-GO until every applicable item in `PROVIDER_ACCEPTANCE.md` is evidenced with that company's sandbox or controlled low-risk credentials**. Source review cannot validate merchant activation, provider tariffs, terminal firmware, external network ACLs, real credentials, provider-side mappings or accounting policy.

“10/10” here means that all repository-controlled production gates below are implemented and pass. It is not a claim that any non-trivial software can be proven to contain no future defect or that third-party services will never change behavior.

## Repository-controlled gates

| # | Area | Result | Evidence |
|---|---|---|---|
| 1 | Clean install and schema | Pass | Frappe 16.25.0 / ERPNext 16.26.2 upgrade migration and diagnostics; 20 required DocTypes found |
| 2 | Authorization | Pass | Every authenticated whitelisted method has an explicit role check; only three signed/secret-protected guest webhooks remain |
| 3 | Secrets and network boundaries | Pass | Password fields, recursive log redaction, HTTPS validation, provider host allowlists and bounded webhook bodies |
| 4 | Idempotency and concurrency | Pass | Durable unique operation ledger, immutable request hashes, row locks/unique-key race recovery and no blind side-effect retry |
| 5 | Financial correctness | Pass | Positive outstanding checks, UAH/company/account validation, exact bank keys, LiqPay v7 signatures and safe reversal reconciliation |
| 6 | Provider failure handling | Pass | Timeouts and ambiguous responses become `unknown`; explicit 4xx/business rejections become `failed`; scheduler failures surface as failed jobs |
| 7 | Data integrity and migration | Pass | Idempotent custom-field migration, unique integration keys, fail-closed profile validation and safe legacy backfill |
| 8 | Operations | Pass | Sanitized bounded logs, retention jobs, diagnostics, monitoring/reconciliation runbook and rollback procedure |
| 9 | Automated quality | Pass | 50 local and 50 native Frappe unit/contract tests; Ruff, compile, Node 24 syntax, Bandit and dependency audit all pass; live read-only Rozetka city/department smoke test passed through the shipped client |
| 10 | CI, packaging and documentation | Pass | `bench build`, wheel and sdist pass; wheel contents include Frappe discovery, Rozetka modules/DocType/assets; CI repeats clean ERPNext v16 migration/tests/build/audit; runbook, migration and provider acceptance docs included |

## Important behavior now enforced

- Financial, shipment, SMS and PBX mutations require idempotency keys.
- A timeout never triggers an automatic financial or external side-effect retry.
- Paid invoices cannot start new LiqPay payment flows.
- New LiqPay checkouts use API v7/SHA3-256; signed v3 callbacks remain supported for migration. A reversal after success requires manual accounting reconciliation and never auto-cancels a submitted Payment Entry.
- Bank imports use exact unique provider-account/transaction keys and reject account-company/currency mismatches.
- Prom.ua imports are all-or-nothing per order and paginate with `last_id`; stock updates use product `external_id` and verify all processed IDs.
- Nova Poshta labels are operation/invoice-authorized, streamed with a size limit and checked as PDF.
- Ukrposhta weight conversion and return enums match the current contract; dynamic URL path identifiers are validated/encoded.
- Rozetka Delivery follows the official create/status/label/directory DTOs, persists unique track IDs, protects mutations with durable idempotency and authorizes PDF labels by invoice/operation.
- TurboSMS and VitalPBX require explicit provider acceptance signals; message bodies are redacted by default and call states cannot regress.
- Customer-identification endpoints enforce POS/Sales roles, hide PII until verification, fail closed on Telegram secrets and preserve ambiguous message outcomes without automatic resend.
- PrivatBank terminal checkout remains in `erpnext_ua.ua_pos`; the removed legacy implementation is not reintroduced by this release.

## Required deployment evidence

Before enabling any live scheduler or mutation, complete `PROVIDER_ACCEPTANCE.md`, record evidence outside the repository, run the migration checklist and confirm:

1. backup restoration has been tested;
2. every process runs the same immutable commit;
3. installation diagnostics return `ok=true`;
4. `bench doctor` and queue/scheduler health are clean;
5. provider sandbox/controlled transactions reconcile exactly with ERP records;
6. alerts exist for failed jobs, `unknown` operations, callback errors and queue backlog.
