# E-commerce Base (0.5.0)

Version 0.5.0 introduces the shared ERPNext-side foundation for provider-specific
channels. It deliberately does not expose or schedule the rejected universal
`Ecommerce Channel` runtime. `OcStore Settings`, `Shop Express Settings` and
`Prom Settings` are delivered in their provider stages and must be ordinary,
multi-record DocTypes linked to `Company`.

Do not deploy the Base branch by itself as a completed ocStore or Shop Express
integration. It is the reviewed migration boundary before those providers are
implemented.

## Shared DocTypes

- `File Delivery Endpoint`: reusable FTP, FTPS or SFTP profile. Hostnames must
  be explicitly listed in `ecommerce_allowed_ftp_hosts`; credentials and SSH
  keys use Frappe `Password` fields.
- `Ecommerce File Layout` + `Ecommerce File Field`: ordered CSV/XML/YML field
  layouts, encodings and transformations.
- `Ecommerce Item Mapping`: ERP Item to provider-instance `external_id` and
  optional `variant_sku`, with a unique `(channel, external_id)` business key.
- `Ecommerce Sync Log`: append-only, redacted terminal/ambiguous run history.
- Reusable child tables: `Ecommerce Sync Entity Config`, `Ecommerce Warehouse
  Sync`, `Ecommerce Payment Route` and `Ecommerce Order Status Map`.

The provider-instance key is `<Settings DocType>:<document name>`, for example
`OcStore Settings:hunter.rv.ua`. This keeps mappings and order keys isolated
when one company has multiple stores.

## Data ownership and hashes

ERPNext `Item` is the product master. Channels receive products, prices, stock,
descriptions and photos; they do not overwrite ERP product master data.

`Ecommerce Item Mapping.last_export_hash` is calculated from the exact payload
produced by the selected serializer and file layout. A change to an Item field
that is absent from the outbound layout does not trigger an export, while a
change to any serialized price, stock, description or photo field does.

`custom-method-path` never imports a dotted path from the database. A transform
must first be registered in application code with
`register_custom_transform(...)`; unregistered values fail validation and fail
again at serialization time.

## File transport

Uploads are written to a temporary remote path and renamed into place. A hidden
checksum sidecar binds the remote file to its immutable idempotency key. Reusing
the key with different content is rejected; a timeout after a possible remote
mutation is `unknown` and is not blindly retried.

SFTP uses strict host-key checking. Install the expected server key in the
runtime user's `known_hosts` file on backend, scheduler and workers before
testing the endpoint. A private key pasted into the Password field may use
escaped `\\n` line endings.

## Order intake

`ecommerce.base.orders.intake(channel, raw_order)` normalizes the customer,
shipping, payment and item rows, resolves products through
`Ecommerce Item Mapping`, deduplicates customers by normalized phone and uses
the configured status action to create standard ERPNext documents.

The immutable order key is derived from `(provider settings instance,
channel_order_id)`. Before creation, intake checks the unique Sales Order/Sales
Invoice fields and a previous successful `Ecommerce Sync Log`. Product mapping
never stores order IDs.

A provider file importer must process every order independently and write a
terminal log. It may delete the inbound FTP file only after every order in that
file was created, found or deliberately ignored and successfully logged. Any
failed row keeps the whole file on FTP with a `Failed` log for manual handling.
This all-or-keep file policy is implemented in the ocStore provider stage.

Stock reservation and accounting remain standard ERPNext behavior. The Base
only creates/submits configured Sales Orders, Sales Invoices and Payment Entries;
it does not replace ERPNext stock reservation, delivery or ledger logic.

## 0.5.0 migration boundary

Before any 0.5.0 production migration, follow `docs/PRODUCTION_RUNBOOK.md` and
take an off-host database and files backup. The Base patch moves
`Ecommerce Item Mapping` metadata, backfills the new fields and creates its
composite unique key idempotently. A pre-model patch also converts the legacy
`Sales Order.ua_ecommerce_channel` custom field from `Link` to `Data`; both use
the same database column, so existing channel names are preserved for the later
provider Settings migration.

Legacy channel DocTypes remain installed in the Base stage as migration sources
but are removed from Desk navigation and scheduler dispatch. Provider stages
must first copy each old channel into the matching multi-record Settings
DocType through an idempotent `patches.txt` patch. Only a later patch may remove
the old DocTypes after successful data migration.
