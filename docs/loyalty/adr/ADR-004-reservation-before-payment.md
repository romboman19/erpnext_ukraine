# ADR-004: Durable reservation до payment

Статус: прийнято.

POS quote не гарантує баланс. Checkout блокує Order і Account, перевіряє row version, створює reservation, переводить її в `PAYMENT_IN_PROGRESS` та commit-ить. Тільки після цього дозволено network call до terminal. Unknown payment утримує lease до reconciliation.
