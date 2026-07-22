# E-commerce Base and ocStore File/XML (0.6.0)

Version 0.5.0 introduces the shared ERPNext-side foundation for provider-specific
channels. It deliberately does not expose or schedule the rejected universal
`Ecommerce Channel` runtime. Provider Settings are ordinary multi-record
DocTypes linked to `Company`; `OcStore Settings` is delivered in 0.6.0, while
Shop Express and Prom move in their later provider stages.

The 0.6.0 ocStore stage implements the ERPNext side of the XML/FTP cycle. The
ocStore site must still have an import/export module configured for the XML
filenames and field contract selected in ERPNext.

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
The ocStore importer implements this all-or-keep policy. It commits every ERP
document and per-order Success log in the file as one transaction, then deletes
the FTP file. Any parsing, mapping, customer, accounting or document error rolls
back the complete file, persists a file-level `Failed` log in a new transaction
and deliberately leaves the remote file untouched.

## ocStore Settings and files

Create one `OcStore Settings` document per shop, for example `hunter.rv.ua` and
`top-trig.store`. Each record has its own Company, price list, customer defaults,
warehouses, payment/status routing, scheduler intervals and endpoints. It is not
a Single DocType.

The migration supplies five editable XML layouts: Products, Prices, Stock,
Photos and Orders. Enabled export entities are published as stable filenames
`<export_file_prefix>-<entity>.xml`. The hash stored on each Item mapping is a
SHA-256 aggregate of the per-entity payload hashes that were actually published;
the hidden state keeps those entity hashes separate so every scheduler interval
works independently. Every component hash is calculated from exactly that
Item's serialized row under the active layout. Changes outside those layouts do
not trigger an export.

When `Export All Enabled Items` is active, the exporter bulk-creates missing
`Ecommerce Item Mapping` rows with `external_id` and `variant_sku` equal to the
ERPNext Item Code. A mapping explicitly marked Disabled is excluded. This avoids
manual one-product-at-a-time setup while retaining an override point for shops
whose external identifier differs.

Photos attached to Item are uploaded unchanged to `Photo FTP Profile`. The
generated public URLs use `Photo URL Prefix`; no remote image URL is fetched by
ERPNext. Catalog feeds use a replaceable atomic publish operation, while every
operation key remains immutable.

The default Orders layout expects `<orders><order>...` with top-level
`order_id`, `status`, `telephone` and nested
`<products><product><external_id|model|product_id>...`. Top-level element names
are configurable in the Orders layout. The clean site-side export should return
the ERP `external_id` or Item model/SKU so the shared Item mapping resolves it
without using ocStore's internal numeric ID.

Stock reservation and accounting remain standard ERPNext behavior. The Base
only creates/submits configured Sales Orders, Sales Invoices and Payment Entries;
it does not replace ERPNext stock reservation, delivery or ledger logic.

## 0.5.0 / 0.6.0 migration boundary

Before any 0.5.0 production migration, follow `docs/PRODUCTION_RUNBOOK.md` and
take an off-host database and files backup. The Base patch moves
`Ecommerce Item Mapping` metadata, backfills the new fields and creates its
composite unique key idempotently. A pre-model patch also converts the legacy
`Sales Order.ua_ecommerce_channel` custom field from `Link` to `Data`; both use
the same database column, so existing channel names are preserved for the later
provider Settings patches.

The 0.6.0 post-model patch creates default layouts, copies every legacy ocStore
channel to a disabled `OcStore Settings` document, migrates its Item mapping
channel keys and recalculates existing SO/SI external-order keys. The new record
remains disabled because legacy channels did not contain `File Delivery Endpoint`
credentials. Configure and test the endpoints before enabling entity rows or the
Settings record.

Legacy channel DocTypes remain installed as migration sources but are removed
from Desk navigation and scheduler dispatch. ocStore records are now copied;
later provider stages must do the same through idempotent `patches.txt` patches.
Only a later patch may remove the old DocTypes after every provider migration is
verified.
