# Changelog

## 0.6.3

- Added Telegram as a native Frappe v16 `Notification` channel without overriding or patching the core controller.
- Added multiple `Telegram Bot Profile` records with encrypted `Password` tokens and manager-scoped permissions.
- Added direct chat IDs plus User, Customer, Employee, Supplier, owner, assignee and role-based recipient resolution.
- Moved Telegram delivery to deduplicated post-commit jobs with durable operation records, explicit failed/unknown classification and no blind retries.
- Added direct in-memory PDF upload through `sendDocument`; no guest print endpoint or reusable document signature is created.
- Added masked timeline entries, bounded provider responses, a fixed Telegram API host, payload redaction, diagnostics and provider acceptance gates.
- Reused the hardened Bot API client for customer-identification messages while keeping its authenticated webhook isolated.

## 0.6.2

- Replace raw ocStore manual-action tracebacks with actionable configuration messages when no matching export or order-import entity is enabled.
- Validate enabled entities in Desk and save changed ocStore settings before testing connections or starting a manual file exchange.

## 0.6.1

- Treat a System Manager-created `File Delivery Endpoint` as explicit authorization for its exact FTP, FTPS or SFTP host, without duplicating the hostname in `site_config.json`.
- Keep hostname, port, path and credential validation intact; SFTP continues to reject server keys missing from the runtime `known_hosts`, and HTTP/API host allowlists are unchanged.

## 0.6.0

- Add ordinary multi-record `OcStore Settings` records linked to Company, with per-entity File/XML schedules and separate exchange/photo endpoints.
- Export configurable products, prices, stock and photo manifests to stable atomically replaced XML feeds; upload attached Item photos unchanged and auto-create missing Item mappings from Item Code.
- Import nested ocStore order XML through the shared idempotent ERP intake and delete each FTP file only after the complete file transaction and per-order logs commit successfully.
- Keep a failed or partially invalid order file on FTP, roll back all new ERP documents from that file and persist an append-only `Failed` sync log.
- Migrate legacy ocStore channels, mapping keys and existing external-order references to provider-instance keys through an idempotent post-model patch while retaining the legacy DocTypes.

## 0.5.0

- Start the provider-specific ecommerce architecture with reusable entity routes, configurable file layouts, exact payload hashing, append-only sync logs and FTP/FTPS/SFTP endpoints.
- Add a fail-closed ecommerce channel contract, registered transform whitelist and durable HTTP/file idempotency primitives.
- Add normalized, phone-deduplicated and order-key-idempotent ERP document intake on top of standard Sales Order reservation and accounting flows.
- Keep legacy channel fields migration-safe until provider Settings migrations are delivered in their own stages.

## 0.4.1

- Force-sync the Ukrainian Integrations Workspace and sidebar so E-commerce channels, mappings and file exchange appear in Desk after upgrading.

## 0.4.0

- Add first-class `Ecommerce Channel` configuration for Shop-Express and ocStore without Torgsoft compatibility or storefront core changes.
- Add Shop-Express API authentication, bounded responses, token refresh, paginated customer/order import, catalog/stock updates and mapped outgoing order statuses.
- Add clean Shop-Express YML and ERPNext Exchange XML v1 catalog/stock exports plus guarded XML order imports for ocStore 3.0.3.7.
- Add per-channel item, customer, warehouse and status mappings, file-exchange history, schedulers, Ukrainian Desk navigation and production activation documentation.

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
