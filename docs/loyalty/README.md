# UA Loyalty

`UA Loyalty` — модуль накопичувальних бонусів усередині Frappe app `erpnext_ua`. Він використовує власні signed bonus/metric ledgers, один рахунок на `(Customer, Scope)`, item-level алокації та durable reservations у POS-UA.

## Головні гарантії

- повернення завжди прив’язане до первинного `Sales Invoice Item` і його алокацій;
- reversal зароблених бонусів може створити debt, але не блокує повернення;
- bonus-paid частина відновлюється на тому самому loyalty account окремо від грошового refund;
- кілька Company/FOP можуть користуватися одним балансом лише через спільний Scope;
- `Sales Invoice` є остаточним posting source, а `POS Order` — saga/quote/reservation source;
- після migrate feature лишається вимкненою.

Початок роботи: створити Scope, Location mapping, Program із tiers, опублікувати snapshot, створити account/card, виконати dry-run імпорту, а потім увімкнути один тестовий POS Cash Desk.

Документи у цій папці описують архітектуру, API, міграцію, тести та recovery. Нормативне рішення щодо inventory provenance/FIFO залишається в `Group Stock FIFO`; loyalty лише переносить `batch_no`, `serial_no`, GSF/POS provenance у власні алокації.
