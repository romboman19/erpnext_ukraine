# Production runbook

## Deployment sequence

1. Back up database and files with `bench --site <site> backup --with-files`; copy the artifacts off-host.
2. Put the site in maintenance mode and stop scheduler/queue consumption.
3. Deploy one immutable commit to every Frappe process.
4. Install the Python package, run `bench --site <site> migrate`, then build assets.
5. Run installation diagnostics and inspect the output; `ok` must be `true`.
6. Restart backend, scheduler and all workers, then run `bench doctor`.
7. Complete read-only smoke tests before enabling provider schedulers or live side effects.

## Required backup boundary for 0.5.0

Do not run the first 0.5.0 migration without a fresh off-host database and files
backup. Record the pre-upgrade app commit and verify that the backup can be
restored. The ecommerce migration is intentionally staged: Base preserves
legacy channel DocTypes as read-only migration sources, provider patches copy
their data into ordinary multi-record Settings, and only a later patch may
remove the old schema. Run `migrate` with scheduler and workers stopped; do not
mix 0.4.x and 0.5.x code across Frappe processes.

For 0.6.0, keep the same backup boundary. The idempotent ocStore patch creates
disabled multi-record Settings and rewrites legacy ocStore mapping/order channel
keys only after the target DocType exists. After migration, review the copied
record, configure allowlisted File Delivery Endpoints, install SFTP host keys in
the runtime user's `known_hosts` on every backend/scheduler/worker, and run Test
FTP Connections before enabling the scheduler.

## Prom.ua site configuration

Prom.ua is intentionally configured by the site administrator:

```json
{
  "prom_ua_enabled": 1,
  "prom_ua_token": "<secret>",
  "prom_ua_api_base": "https://my.prom.ua/api/v1",
  "prom_ua_currency": "UAH",
  "prom_ua_company": "My Company",
  "prom_ua_customer_group": "Commercial",
  "prom_ua_territory": "Ukraine",
  "prom_ua_warehouses": ["Main Warehouse - MC"],
  "prom_ua_orders_status": "pending",
  "prom_ua_orders_limit": 50,
  "prom_ua_orders_max_pages": 20,
  "prom_ua_stock_batch_size": 500,
  "prom_ua_stock_max_items": 100000
}
```

`Item.name` must equal Prom's product `external_id`. Only the explicit warehouse list contributes to marketplace quantity. Validate the mapping on a sandbox company before enabling stock sync.
An order is rejected as a whole if any product row is unmapped or invalid; the importer never creates a partial Sales Order. Custom Prom API hosts also require `prom_ua_allowed_api_hosts`.

## Rozetka Delivery activation

Create `RZ Delivery Sender Profile` records before enabling live creation. Use a static partner token, select sender city and department through the profile action, assign a Company where the site serves multiple legal entities, and verify the token with the read-only action. The integration intentionally supports `dept-dept` shipments; door delivery is rejected until its separate address workflow is implemented and accepted.

The scheduler polls non-terminal Sales Invoice tracks every 30 minutes. A provider timeout or 5xx during creation remains `unknown` and must be reconciled in the Rozetka Delivery partner cabinet by `visible_id` (the Sales Invoice name) before any manual resolution. See `docs/ROZETKA_DELIVERY.md`.

## Webhooks and network controls

- Terminate TLS at a trusted reverse proxy and restrict request body size.
- LiqPay must call the documented callback URL over HTTPS.
- New LiqPay checkouts default to API v7/SHA3-256. The callback verifier also accepts signed v3 payloads so checkouts issued immediately before an upgrade can finish safely; do not force `liqpay_api_version=3` except for a time-bounded rollback.
- VitalPBX must send `X-Webhook-Key`; use a random 32+ byte secret and, where possible, an IP allowlist at the proxy.
- Telegram must send `X-Telegram-Bot-Api-Secret-Token`; the endpoint rejects requests when the configured secret is absent.
- Restrict outbound egress to official provider endpoints and explicitly approved gateway/sandbox hosts.
- Allow Rozetka Delivery traffic only to `rz-delivery.rozetka.ua` unless a controlled sandbox host is explicitly listed in `rozetka_delivery_allowed_api_hosts`.
- Keep `vitalpbx_allow_query_key`, `vitalpbx_allow_insecure_recording_url` and `vitalpbx_verify_ssl=0` disabled unless a documented private-network exception exists.

## Reconciliation

Review `UA Integration Operation` at least daily:

- `started`: checkout generated or work reserved but not terminal.
- `unknown`: provider may have completed the side effect; reconcile externally before any new attempt.
- `failed`: explicit provider/business rejection; correct the cause and use a new idempotency key for a genuinely new attempt.
- `verified`: LiqPay payment verified but not necessarily booked.
- `reconciled`: external payment and ERP accounting entry linked.
- `succeeded`: non-accounting side effect confirmed.

A LiqPay `reversed` callback received after verification deliberately moves the ledger back to `unknown` and requires an operator to reconcile or reverse the related Payment Entry according to the acquirer journal. The callback never auto-cancels submitted accounting documents.

For manual resolution, record the provider identifier and a concise evidence note. Do not mark a financial operation successful from a browser screenshot alone; use the provider/acquirer journal.

## Monitoring

Alert on:

- any scheduler result with `ok=false`;
- new `Hunter Integration Log.status=error`;
- `unknown` operations older than 15 minutes;
- queue backlog, failed jobs, scheduler inactivity and worker restarts;
- repeated webhook 401/409/413 responses at the proxy;
- unexpected growth in integration/call/SMS logs.
- Rozetka Delivery profiles approaching credential rotation or repeated 401 responses.

Default retention is 180 days for integration/SMS logs and 365 days for VitalPBX call logs. Override with `ua_integration_log_retention_days` and `vitalpbx_call_log_retention_days` according to legal policy. Backups have a separate retention policy.
TurboSMS message bodies are redacted by default; `turbosms_store_message_text=1` is an explicit privacy-sensitive opt-in.

## Secrets rotation

1. Create the new provider credential.
2. Pause the affected scheduler/side effects.
3. Update the Password field or site configuration on the active site.
4. Run a non-mutating health/read test where available.
5. Resume and observe one full scheduler interval.
6. Revoke the old credential and verify logs contain no secret material.

Rotate the VitalPBX webhook key on both systems in one maintenance window. LiqPay private-key or API-version rotation requires keeping callbacks for already-issued checkouts in mind; finish or explicitly reconcile old checkout operations first.

## Rollback

Stop traffic and workers. Restore the pre-deployment database and files, deploy the recorded previous source commit to every process, reinstall the package, rebuild assets and restart. A code-only rollback after schema/data migration is unsupported.
