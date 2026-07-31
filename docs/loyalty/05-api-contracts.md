# API contracts

Namespace: `erpnext_ua.ua_loyalty.api`. Mutation endpoints приймають POST і, де створюється економічний стан, `idempotency_key`.

- `identify(pos_session_token, identifier, identifier_type)` — account/card/balances/tier.
- `enroll(pos_session_token, customer, barcode)` — account/card з permission guard.
- `quote(pos_session_token, source_name, requested_redemption)` — canonical row-level quote/hash.
- `reserve(..., quote_hash, idempotency_key)` — atomic balance reservation.
- `mark_payment_in_progress(...)` — продовжує lease перед terminal call.
- `release(..., reason_code, idempotency_key)` — не звільняє unresolved payment без recovery.
- `account_summary(account)`, `statement(account, limit, cursor)` — read APIs.
- `card_action(card, action, reason, replacement_barcode, idempotency_key)` — audited lifecycle.
- `request_adjustment`, `approve_adjustment`, `reject_adjustment` — dual-control posting.
- `run_import(batch)` — dry-run або opening ledger import.
- `reconcile(account, repair)` — preview; repair лише Administrator.
- `publish_program(program)` — immutable rule snapshot.

Грошові значення у JSON повертаються decimal strings. Помилки мають стабільні `LOYALTY_*` titles/codes.
