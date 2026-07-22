# ERPNext Ukrainian Integrations

Production-oriented Frappe/ERPNext v16 application for Ukrainian shipping, payments, telephony, SMS and marketplace workflows.

## Supported stack

- Frappe / ERPNext: v16
- Python: 3.11+
- MariaDB: 10.6+
- Redis: 6+
- App version: 0.6.3

The repository is validated by static analysis, unit/contract tests, package build, JavaScript parsing, and a clean ERPNext v16 install/migrate job. A deployment is production-ready only after the provider acceptance checklist has passed with the organization's own sandbox or low-risk credentials; no repository can prove third-party credentials, tariffs, terminal firmware, network ACLs, or merchant-account settings in isolation.

## Integrations

- Nova Poshta: sender profiles, settlement/warehouse lookup, TTN creation, tracking, label proxy.
- Ukrposhta eCom: sender profiles, address/client/shipment flow and tracking.
- Rozetka Delivery: static-token sender profiles, city/department lookup, department-to-department shipment creation, COD, tracking and PDF labels.
- Monobank and PrivatBank Autoclient: paginated statement import into `Bank Transaction`.
- LiqPay: deterministic checkout initiation, signed callbacks and optional Payment Entry reconciliation.
- VitalPBX: click-to-call, authenticated event webhook, extension-scoped call logs and realtime popups.
- TurboSMS: sender allowlist, idempotent send API and delivery request logging.
- Telegram: a native Frappe v16 `Notification` channel with multiple encrypted bot profiles, role/party/direct-chat recipients and direct PDF upload.
- Prom.ua: `last_id` order pagination and stock updates by product `external_id`.
- E-commerce Base: provider-specific channel contract, configurable CSV/XML/YML layouts, FTP/FTPS/SFTP endpoints, payload-based export hashes, item mapping, append-only sync logs and idempotent ERP order intake.
- ocStore 3.0.3.7: multi-store `OcStore Settings`, configurable XML catalog/price/stock/photo feeds, unchanged photo uploads, scheduled FTP import of nested order XML and all-or-keep file transactions.
- Customer identification: SMS, Telegram and inbound-call verification with rate limits, PII gating and birthday messaging.

PrivatBank terminal checkout is owned by `erpnext_ua.ua_pos`; this app keeps only the migration guide and external connectors.

## Safety model

Externally mutating provider calls use immutable idempotency keys or a durable business key. Reusing a key with different parameters is rejected. A timeout or malformed response is recorded as `unknown`; the application does not blindly retry financial, shipment, SMS, messaging, or call side effects.

Use `UA Integration Operation` to inspect ambiguous operations. Accounting managers can access only payment/bank operations; sales managers can access only logistics, marketplace, SMS and PBX operations. Manual resolution is available through `ukrainian_integrations.utils.operations.resolve_operation` and must follow provider-side reconciliation.

Secrets are stored in Frappe `Password` fields, sensitive payload keys and URLs are redacted from logs, and provider-configurable hosts are allowlisted. `Hunter Integration Log` and `UA Integration Operation` are append-only in Desk.

## Installation

```bash
cd /home/frappe/frappe-bench
bench get-app https://github.com/romboman19/erpnext_ukraine_integrations.git
./env/bin/pip install -e apps/erpnext_ukraine_integrations
bench --site <site> install-app ukrainian_integrations
bench --site <site> migrate
bench --site <site> execute ukrainian_integrations.diagnostics.run_installation_checks
bench build --app ukrainian_integrations
```

The app directory must be mounted and installed in backend, scheduler and all queue workers. Restart those processes after migration and asset build.

For an upgrade, take a database and files backup first, deploy the same commit to every process, install its Python package, and then run `bench --site <site> migrate`. See [Production runbook](docs/PRODUCTION_RUNBOOK.md) and [Migration checklist](docs/MIGRATION_CHECKLIST.md).

## Configuration

Prefer the Settings/Profile DocTypes in Desk. When those DocTypes contain configuration, missing credentials fail closed and do not silently fall back to `site_config.json`.

| Domain | Configuration |
|---|---|
| Nova Poshta | `NP Sender Profile` with API key, sender/contact refs, phone and branch/address refs |
| Ukrposhta | `UP Sender Profile` with eCom bearer, optional tracking/counterparty tokens and sender address |
| Rozetka Delivery | `RZ Delivery Sender Profile` with static bearer token, sender person, city/department UUIDs and optional Company |
| Monobank | `Monobank Settings` → enabled profiles with token, provider account, ERP Bank Account and Company |
| PrivatBank | `PrivatBank Settings` → profiles; API host defaults to `acp.privatbank.ua` |
| LiqPay | `LiqPay Settings` → profiles; API v7/SHA3-256 is the default; keep `Accept Sandbox Callbacks` off in production |
| VitalPBX | `VitalPBX Settings`, unique webhook key, and `User.vitalpbx_extension` |
| TurboSMS | `TurboSMS Settings` with official API URL, token and active sender rows |
| Telegram notifications | `Telegram Bot Profile`; configure recipients and templates in the standard `Notification` DocType |
| Customer identification | `Identification Channel Settings`; POS defaults to SMS through TurboSMS, while Telegram and VitalPBX remain optional |
| Prom.ua | `site_config.json`; see the runbook for the complete key list |
| E-commerce / ocStore | One `OcStore Settings` record per shop and Company; `File Delivery Endpoint`, XML layouts, warehouses, payment/status routes and scheduler intervals are selected inside it |

Custom non-production provider hosts must be explicitly allowlisted in `site_config.json`:

```json
{
  "privatbank_allowed_api_hosts": ["sandbox.example.internal"],
  "ukrposhta_allowed_api_hosts": ["sandbox.example.internal"],
  "rozetka_delivery_allowed_api_hosts": ["sandbox.example.internal"],
  "turbosms_allowed_api_hosts": ["sandbox.example.internal"],
  "prom_ua_allowed_api_hosts": ["sandbox.example.internal"],
  "shop_express_allowed_api_hosts": ["shop.example.ua"],
  "ecommerce_allowed_ftp_hosts": ["ftp.example.ua"]
}
```

Do not allowlist hosts that are not controlled by the organization or the provider: the corresponding credential is sent to that host.

TurboSMS message bodies are redacted in `TurboSMS Log` by default. Set `turbosms_store_message_text=1` only after a documented privacy/retention review; the operation ledger never stores the body itself.

Telegram notification tokens are separate from the customer-identification webhook bot. Outbound sends run in a worker, never retry an ambiguous result, and upload print PDFs directly to Telegram instead of creating guest-accessible document URLs. See [Telegram channel setup](docs/TELEGRAM.md).

## Webhooks

LiqPay callback:

```text
/api/method/ukrainian_integrations.payments.liqpay.service.liqpay_callback
```

The callback accepts only a valid LiqPay signature, known order, matching public key, `pay` action, amount and currency. `sandbox` is rejected unless explicitly enabled on the selected profile.
New checkouts use LiqPay API v7. Set `liqpay_api_version=3` only for a documented temporary rollback; callback verification remains compatible with already-issued v3 checkouts during migration.

VitalPBX webhook:

```text
/api/method/ukrainian_integrations.pbx_sms.vitalpbx.events.webhook_event
X-Webhook-Key: <random-secret>
```

Query-string secrets are disabled by default. If legacy infrastructure temporarily requires them, set `vitalpbx_allow_query_key=1` and plan a migration to the header.

Telegram customer-identification webhook:

```text
/api/method/ukrainian_integrations.customer_identification.telegram.webhook
X-Telegram-Bot-Api-Secret-Token: <random-secret>
```

The Telegram endpoint fails closed when the secret is absent and bounds request/response bodies.

UA POS starts verification through the policy-aware endpoint:

```text
/api/method/ukrainian_integrations.customer_identification.service.begin_pos
```

The administrator selects the POS channel in `Identification Channel Settings`.
The default is locked to `SMS` (TurboSMS); cashier channel selection is an explicit
opt-in and disabled by default.

## Verification

```bash
python -m compileall -q ukrainian_integrations
ruff check ukrainian_integrations
bandit -q -r ukrainian_integrations -x ukrainian_integrations/tests
pip-audit --strict --progress-spinner off .
python -m unittest discover -s ukrainian_integrations/tests -p "test*.py" -v
find ukrainian_integrations/public -name "*.js" -print0 | xargs -0 -n1 node --check
python -m build
bench --site <site> execute ukrainian_integrations.diagnostics.run_installation_checks
```

CI repeats these gates and installs/migrates the app on a clean ERPNext v16 site.

## Operations and support

- Read the [production-readiness report](docs/PRODUCTION_READINESS_REPORT.md) for the repository-level decision, evidence and deployment boundary.
- Follow [Production runbook](docs/PRODUCTION_RUNBOOK.md) for backup, deployment, monitoring, reconciliation and rollback.
- Follow [Provider acceptance](docs/PROVIDER_ACCEPTANCE.md) before enabling any scheduler or live side effect.
- See [Rozetka Delivery setup](docs/ROZETKA_DELIVERY.md) for profile configuration, workflow and acceptance steps.
- See [E-commerce and ocStore](docs/ECOMMERCE_CHANNELS.md) for the shared contracts, XML formats and staged provider migration boundary.
- See [Telegram channel setup](docs/TELEGRAM.md) for bot profiles, recipients, notifications and reconciliation.
- See [Privat POS migration](docs/privat_pos_flow.md) for the move to `erpnext_ua.ua_pos`.
- Security reporting and supported versions are in [SECURITY.md](SECURITY.md).
- Release history is in [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE).
