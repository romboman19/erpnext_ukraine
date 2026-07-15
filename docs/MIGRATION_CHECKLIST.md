# Upgrade checklist: 0.1.x / 0.2.x → 0.3.0

## Before maintenance

- [ ] Record the current app and ERPNext commits.
- [ ] Run `bench --site <site> backup --with-files` and copy the backup off-host.
- [ ] Confirm a tested restore path and maintenance window.
- [ ] Stop schedulers/workers or put the site in maintenance mode.
- [ ] Export current `site_config.json` securely; never commit it.

## Deploy

- [ ] Deploy the same 0.3.0 commit to backend, scheduler and every queue worker.
- [ ] Run `./env/bin/pip install -e apps/erpnext_ukraine_integrations`.
- [ ] Confirm `./env/bin/python -c "import requests; print(requests.__version__)"` reports 2.33.0 or newer.
- [ ] Run `bench --site <site> migrate` once.
- [ ] Run `bench build --app ukrainian_integrations`.
- [ ] Restart backend, scheduler and workers.
- [ ] Run `bench --site <site> execute ukrainian_integrations.diagnostics.run_installation_checks`.

Migration 0.2.0 creates deterministic unique keys for bank transactions, customers and sales orders. Legacy bank keys are backfilled only when the provider account can be mapped unambiguously from a profile. Ambiguous records are intentionally left for manual review.

Migration 0.3.0 adds `RZ Delivery Sender Profile` and unique Rozetka Delivery fields to Sales Invoice. Run migration before any shipment creation so a confirmed provider track can always be persisted locally.

## Reconfigure

- [ ] Populate and enable explicit NP/UP sender profiles.
- [ ] Populate `RZ Delivery Sender Profile`, select sender city/department, verify the static token and assign Company where applicable.
- [ ] Populate Monobank, PrivatBank and LiqPay profile rows; verify Company/Bank Account ownership.
- [ ] Populate TurboSMS Settings; do not rely on legacy fallback after the DocType exists.
- [ ] Configure `Customer Identification Settings`; Telegram must have both a bot token and webhook secret.
- [ ] If `erpnext_ua.ua_pos` is deployed, complete the separate PB POS migration in `docs/privat_pos_flow.md`.
- [ ] Set `User.vitalpbx_extension` and rotate the VitalPBX webhook key.
- [ ] Configure all required Prom.ua keys, including `prom_ua_company` and warehouse allowlist.
- [ ] Keep sandbox callbacks and customer-identification test mode disabled in production.
- [ ] Confirm new LiqPay checkout payloads use API v7; do not set `liqpay_api_version=3` except for a time-bounded rollback (old v3 callbacks remain verifiable automatically).

## Validate before schedulers

- [ ] Complete every applicable item in `docs/PROVIDER_ACCEPTANCE.md`.
- [ ] Inspect `UA Integration Operation` for stale `started`/`unknown` records.
- [ ] Confirm duplicate legacy Prom orders were not present by `po_no`.
- [ ] Confirm bank transaction counts for a known date range before and after import.
- [ ] Confirm scheduler and worker queues are healthy with `bench doctor`.

## Rollback

Do not roll back only the source code after a migration. Stop traffic and workers, restore the pre-upgrade database and files backup, deploy the recorded previous app commit to every process, reinstall it, rebuild assets and restart. Verify `list-apps`, worker health and a read-only business report before reopening the site.
