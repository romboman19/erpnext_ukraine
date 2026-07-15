# Changelog

## 0.3.4

- Add an explicit identification-channel settings menu and administrator-controlled default/POS channel routing.
- Lock UA POS to SMS via TurboSMS by default, with optional cashier channel selection.
- Add the stable `begin_pos` API and authorize the UA POS cashier roles without weakening customer PII gating.

## 0.3.3

- Merge newly installed custom application icons into existing Frappe v16 user desktop layouts without resetting user ordering, folders or hidden icons.
- Invalidate navigation caches for every user after app installation and migration.
- Add a Ukrainian overlay for common Frappe Desk navigation, actions and messages that are missing from the upstream v16 language pack.

## 0.3.2

- Added complete Ukrainian labels for the Frappe v16 System Health Report and its child tables.
- Corrected Docker false negatives for live RQ workers that do not persist a PID.
- Added a shared Redis scheduler heartbeat so the report can distinguish an isolated container filesystem from a stopped scheduler.

## 0.3.0

- Merged the current customer-identification/POS-support line and retained its newer customer workflows.
- Hardened SMS, Telegram and inbound-call verification with explicit roles, PII gating, rate limits, row locks, replay-safe messaging and fail-closed webhook secrets.
- Kept PrivatBank terminal checkout in `erpnext_ua.ua_pos` instead of reintroducing the removed legacy implementation.
- Added production-oriented Rozetka Delivery integration against the official RZ-Delivery OpenAPI contract.
- Added static-token sender profiles, Company isolation, sender city/department selection and HTTPS host allowlisting.
- Added department-to-department Sales Invoice and manager-only standalone track creation with weight/dimensions, payer, insured value and COD validation.
- Added durable idempotency, per-invoice conflict prevention, explicit-rejection versus unknown-outcome handling and no blind mutation retries.
- Added unique Sales Invoice track IDs, status code/name, shipping cost, payment fee, estimated delivery date and sender-profile fields.
- Added manual and scheduled status synchronization plus invoice/operation-authorized, bounded and PDF-validated label downloads.
- Added Rozetka Delivery API contract tests, provider acceptance gates and deployment documentation.

## 0.2.0

- Added durable operation ledger with immutable request hashes and reconciliation states.
- Added role checks to all whitelisted methods and signature/secret validation to guest webhooks.
- Added missing Settings/Profile/Terminal/Sender/Log DocTypes and idempotent custom-field migration.
- Added exact unique bank transaction keys, profile/company validation and reliable pagination.
- Upgraded new LiqPay checkout signatures to API v7/SHA3-256 while retaining v3 callback migration compatibility; hardened callback concurrency, reversal handling, state transitions, amount/currency/action checks and optional reconciliation.
- Removed the legacy PB POS implementation after its migration to `erpnext_ua.ua_pos`.
- Added shipment/SMS/call idempotency and unknown-outcome handling.
- Bound Nova Poshta labels to authorized operations and removed API-key prefix diagnostics.
- Corrected Prom.ua `last_id` pagination and external-ID stock update contract.
- Corrected Ukrposhta kilograms-to-grams conversion and current `onFailReceiveType` validation.
- Added extension-scoped VitalPBX logs, monotonic webhook states, redaction and retention.
- Added unit/contract/static/dependency-security gates and clean ERPNext v16 install/migrate CI.

## 0.1.0

- Initial integration skeleton and provider workflows.
