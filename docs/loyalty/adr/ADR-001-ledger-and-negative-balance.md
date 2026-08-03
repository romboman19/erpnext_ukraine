# ADR-001: Signed append-only ledger

Статус: прийнято.

Баланс є сумою append-only `active_delta`; cache в Account не є джерелом істини. Reversal повернення не має floor zero, тому `20 - 60 = -40`. Redeemable визначається як `max(marketing - reserved, 0)`, debt — `max(-marketing, 0)`. Виправлення виконуються inverse entries.
