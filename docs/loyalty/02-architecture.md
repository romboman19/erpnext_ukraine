# Архітектура

Потік продажу:

```text
POS Order -> quote(snapshot + row allocations) -> reservation
          -> commit -> terminal/payment -> Sales Invoice submit
          -> ledger + metric + immutable allocations -> fiscal receipt
```

Потік повернення:

```text
receipt barcode -> original POS Order -> original Sales Invoice row
                -> original EARN/REDEEM allocations
                -> EARN reversal + REDEEM restore -> signed balance
```

`domain/` містить Decimal-формули без Frappe I/O. `services/` виконує locks, idempotency та append-only posting. `adapters/` переносить POS/GSF provenance в `Sales Invoice`. Hooks не містять формул. POS JavaScript лише відображає серверний quote.

Порядок блокувань: source document, account, reservation/obligation. Зовнішній terminal викликається тільки після commit reservation lease. Повторний submit має ті самі logical idempotency keys.
