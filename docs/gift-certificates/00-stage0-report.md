# Stage 0 report

Дата доказу: 2026-08-01. Target: ERPNext/Frappe v16, MariaDB, `postest.local` Docker bench.

| Proof | Рішення та evidence |
|---|---|
| Sales Invoice Payment GL | Passed: liability/expense payment modes проводяться через standard SI POS GL. Integration test перевіряє debit liability 100.00. Invoice отримує Cost Center з Accounting Profile. |
| Return/cancel | Passed для return: standard return SI створює протилежний payment GL; credit liability 100.00. Gift restore йде з immutable allocation. Cancel створює inverse ledger/settlement, не delete. |
| Certificate sale | Обрано `UA Gift Certificate Sale + Journal Entry`: cash/bank debit, paid liability credit, premium income окремо; activation тільки після JE/payment evidence. |
| Hook order | `before_submit`: GSF/ownership → loyalty → gift validation. `on_submit`: ERPNext GL → consignment → loyalty → gift consume → UA Fiscal. Direct manual redemption blocked. |
| Multi-FOP | Item allocation зберігає issuer/redeemer FOP. Same entity не створює settlement; інша entity вимагає flag + active Settlement Profile та створює signed settlement rows. |
| PRRO mapping | Gift payment є server-generated payment row. Fiscal adapter snapshot-ує суму/форму; unsupported VAT profile завершується fail-closed до оплати. Live PRRO sign-off потрібен під час pilot. |
| Compliance | Resolver перевіряє company/FOP/profile/date/action до issue, sale або redemption. Відсутній/спрощений/VAT unsupported profile блокує операцію. |
| Locks/idempotency | POS Order → certificates sorted → reservations. MariaDB `FOR UPDATE`, row_version quote check, unique idempotency fields, durable reservation commit до terminal call. |
| HMAC/encryption/print | HMAC lookup, Password ciphertext, masked API. One-time Print Grant повертає token лише protected `no-store` view; grant append-only; `print_token_once` вимагає replacement. |
| Fault boundaries | Reservation committed до terminal; SI/ledger/allocations idempotent; PRRO uncertainty переходить у Fiscal Pending; blind external retry заборонений. |

Acceptance evidence:

- `test_discounted_certificate_sale_redemption_and_three_partial_returns`: 300 face / 240 sale, redeem 200, restore 66.67/66.67/66.66, final paid/promo 240/60.
- `test_sales_invoice_payment_posts_liability_and_reverses_on_return`: final GL account signs.
- `test_print_grant_exposes_token_once_and_requires_replacement_for_reprint`.
- `test_batch_generation_is_idempotent`.

Stage 0 approval у Settings є людським контрольним рішенням. Автоматичний readiness не підміняє підпис бухгалтера/compliance owner і pilot PRRO evidence.
