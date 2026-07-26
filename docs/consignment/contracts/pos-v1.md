# POS integration contract v1

## Статус

Implemented у release `1.0.0`. Persistent `CC POS Checkout`, `CC POS Route`,
`CC POS Route Payment` і `CC POS Print Job`, permission-aware API, retries,
compensation та full Frappe integration tests реалізовані. Production activation
потребує прийнятого зовнішнього POS/fiscal/print adapter і залишається
deployment decision.

## Підтверджена межа POS

На `postest.local` встановлено `erpnext_ua` POS Order із `idem_key`,
`lookup_token`, одним `sales_invoice`, одним `fiscal_mode`, items і єдиним
logical payment plan. Він лишається коренем checkout, але 1:N split state
зберігається у власних route-документах цього застосунку.

Один route має стабільний ключ:

```text
external order × Company × legal entity × fiscal route
```

Route володіє посиланням на свій Sales Invoice, статусом submit/compensation і
окремим idempotent print job. Retry читає route перед створенням SI. POS adapter
повертає агрегований список sub-documents, не переписуючи single-SI поле
поточного POS Order.

## Напрям залежності

POS є оркестратором checkout. Застосунок стороннього товару є власником
allocation, partner pricing і fiscal-policy рішення для своїх рядків.

```text
external POS order
  -> sales.reserve
  -> pos.prepare
  -> pos.advance_route (idempotent create/submit Sales Invoice)
  -> persistent print job
  -> pos.print_succeeded / pos.print_failed
  -> pos.set_payment_state
```

Прямі імпорти приватних service-модулів між apps заборонені. POS використовує
versioned whitelisted API або зареєстрований adapter hook.

## Операції

### `sales.reserve`

Вхід:

```json
{
  "v": 1,
  "company": "Example Company",
  "location": "STORE-001",
  "item_code": "ITEM-001",
  "qty": 1,
  "allowed_warehouses": ["Third Party - EX"],
  "idempotency_key": "checkout-id:reserve:1"
}
```

Вихід містить server-owned allocation та exact FIFO slices. Preview без
резервування доступний як внутрішній read-only candidate/reporting adapter і не
є write boundary checkout.

### `pos.prepare`

Створює immutable checkout snapshot, перевіряє суми tender, fiscal policy,
legal entity та групування вже зарезервованих allocation. Повтор із тим самим
idempotency key і payload повертає той самий checkout; інший payload
відхиляється як idempotency conflict.

### `pos.advance_route`

Ідемпотентно просуває один route через створення/submit керованого Sales
Invoice до durable print job. Retry читає persisted state перед кожним write.

### Print/payment/compensation

`pos.print_succeeded`/`pos.print_failed` зберігають provider outcome;
`pos.set_payment_state` фіксує зовнішній payment state; `pos.compensate`
скасовує безпечні submitted documents і звільняє резерви. Captured або
невідомий payment state не компенсується всліпу та вимагає manual review.

## Помилки

```json
{
  "error_code": "UA_TP_ALLOCATION_CONFLICT",
  "message_uk": "Залишок уже зарезервовано іншим продажем",
  "details": {},
  "recoverable": true,
  "correlation_id": "..."
}
```

## Інваріанти

- reserve не створює бухгалтерських проводок;
- prepare не створює Stock Ledger або GL Entries;
- route не може змінити submitted Sales Invoice;
- повторний checkout не створює дубль SI, allocation або JE;
- відсутність цього застосунку не блокує продаж власного товару в POS;
- відсутність POS не блокує контрольований ручний Sales Invoice flow.
