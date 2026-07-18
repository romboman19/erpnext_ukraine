# E-commerce channels

This app owns ERPNext-side exchange for Shop-Express and ocStore. It has no
Torgsoft compatibility layer and does not install or modify code in ocStore.

## Data ownership

- ERPNext owns item codes, prices, available stock and outgoing order statuses.
- Shop-Express and ocStore own checkout capture and the original external order ID.
- An external order is created at most once. `Sales Order.ua_external_order_key`
  is deterministic and unique, while the readable channel and provider order ID
  are kept in `ua_ecommerce_channel` and `ua_external_order_id`.
- Item and customer identifiers are kept in channel-specific mapping DocTypes.

Available stock is calculated as the sum of `Bin.actual_qty - Bin.reserved_qty`
for the mapped warehouses, minus the configured safety stock. Negative results
are exported as zero.

## Shop-Express

Recommended routes:

| Entity | Transport |
|---|---|
| Full catalog | XML (`Shop-Express YML`) |
| Prices and stock | API |
| Orders | API |
| Customers | API |
| Order statuses | API, after status mappings are accepted |

Configure `shop_express_allowed_api_hosts` in `site_config.json` before enabling
the channel. The API base URL must use HTTPS and contain no embedded credentials.

```json
{
  "shop_express_allowed_api_hosts": ["shop.example.ua"]
}
```

The client authenticates through `/api/auth`, caches the ten-minute token for
nine minutes and re-authenticates once after an explicit `UNAUTHORIZED`
response. Network timeouts for mutating requests are not retried.

The first order/customer pull uses `Initial Sync Days`. Later pulls overlap the
previous watermark by `Orders Overlap (minutes)`. ERPNext advances a watermark
only after all rows in the page range are handled successfully.

## ocStore 3.0.3.7

Use `XML` or `Disabled` for all routes. Generate catalog and price/stock files
from the `Ecommerce Channel` form. Import an order file by creating an
`Ecommerce File Exchange` with:

- Direction: `Import`
- Entity: `Orders`
- File Format: `XML`
- Profile: `ERPNext Exchange XML v1`

Attach the file exported by the ocStore import/export module and run
`Process Import`. Configure that module's XML template to emit the clean schema
below. No database access or ocStore core modification is required.

## ERPNext Exchange XML v1

Catalog exports use this root:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ecommerce_exchange
  schema="erpnext-ecommerce-v1"
  entity="catalog"
  channel="ocstore-main"
  generated_at="2026-07-18T12:00:00+00:00">
  <categories>
    <category id="1" parent_id="" name="Одяг" />
  </categories>
  <products>
    <product id="1001" sku="SKU-1001" available="1">
      <name>Товар</name>
      <category_id>1</category_id>
      <price currency="UAH">499.00</price>
      <quantity>8</quantity>
      <pictures>
        <picture>https://erp.example.ua/files/item.jpg</picture>
      </pictures>
    </product>
  </products>
</ecommerce_exchange>
```

Order imports use one `order` element per order and one `item` per product row:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<ecommerce_exchange
  schema="erpnext-ecommerce-v1"
  entity="orders"
  channel="ocstore-main">
  <orders>
    <order id="157" number="OC-157" created_at="2026-07-18 11:45:00"
           currency="UAH" status="Processing" paid="0">
      <customer id="42">
        <name>Іван Петренко</name>
        <phone>+380501234567</phone>
        <email>customer@example.ua</email>
      </customer>
      <delivery>
        <city>Рівне</city>
        <address>Відділення 1</address>
      </delivery>
      <comment>Зателефонувати перед відправленням</comment>
      <items>
        <item sku="SKU-1001" quantity="2" price="499.00" />
      </items>
    </order>
  </orders>
</ecommerce_exchange>
```

The parser also accepts common ocStore aliases such as `order_id`,
`order_number`, `telephone`, `model`, `qty`, `date_added`, `shipping_city` and
`shipping_address`. DTDs and XML entities are rejected, the file size is
bounded, and an order with an invalid or unmapped product is never partially
created.

## Production activation

1. Create the channel disabled.
2. Add the ERP warehouse rows and a selling price list.
3. Add enabled `Ecommerce Item Mapping` rows. `Export Only Mapped Items` is on by
   default and should remain on in production.
4. Generate a catalog file and validate it in a staging store.
5. For Shop-Express, test API authorization and run customer/order synchronization
   manually with a short initial period.
6. Verify customer reuse, item mappings, taxes, currency and delivery details in
   draft Sales Orders.
7. Enable stock synchronization.
8. Add status mappings last, then enable outgoing status synchronization.

Use `Ecommerce File Exchange` for file history and `Hunter Integration Log` for
API/import diagnostics. A provider `WARNING`, incomplete status log, unknown SKU
or currency mismatch fails the corresponding run instead of silently dropping
data.
