# Changelog

## 0.3.0

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
- Removed automatic POS protocol retries/fallback; added UAH/outstanding checks and terminal locking.
- Added shipment/SMS/call idempotency and unknown-outcome handling.
- Bound Nova Poshta labels to authorized operations and removed API-key prefix diagnostics.
- Corrected Prom.ua `last_id` pagination and external-ID stock update contract.
- Corrected Ukrposhta kilograms-to-grams conversion and current `onFailReceiveType` validation.
- Added extension-scoped VitalPBX logs, monotonic webhook states, redaction and retention.
- Added unit/contract/static/dependency-security gates and clean ERPNext v16 install/migrate CI.

## 0.1.0

- Initial integration skeleton and provider workflows.
