# Stage 0 — протокол технічного spike

## Мета

Підтвердити найризикованіші припущення на ізольованому ERPNext v16 site до
реєстрації production hooks і створення повної моделі DocType.

Результатом кожного spike є:

1. відтворюваний test fixture або script;
2. фактичні Stock Ledger/GL записи;
3. ADR із рішенням і fallback;
4. автоматизований regression test;
5. статус `PASS`, `FAIL` або `BLOCKED` — без оптимістичних припущень.

## Gate 0A — середовище

- [x] `frappe`, `erpnext` і Python versions зафіксовані;
- [x] `bench migrate` проходить на test site;
- [x] readiness API не має blocking checks;
- [x] scheduler і workers доступні;
- [x] backup/restore test site перевірено.

Поточний evidence: [`2026-07-13-gate-0a.md`](evidence/2026-07-13-gate-0a.md).

Gate 0A result: `PASS`. Окремий scheduler працює в `frappe-test`, один worker
online, а backup із файлами відновлено в paused-копію `postest-restore.local`
з ідентичним контрольним fingerprint.

## Gate 0B — Inventory Dimension

- [x] dimension field проходить через Stock Entry Detail;
- [x] dimension зберігається у Stock Ledger Entry;
- [x] Sales Invoice `update_stock=1` переносить dimension;
- [x] перевірено Serial and Batch Bundle (`NATIVE FAIL`, контрольований fallback `PASS`);
- [x] перевірено Material Transfer, Return і Stock Reconciliation;
- [x] перевірено negative stock validation для dimension;
- [x] виміряно query/report performance на 250 000 рядків.

Fallback дозволяє lot custom link і контрольоване bundle mapping, але не
створення паралельного stock ledger.

Поточний evidence:
[`2026-07-13-gate-0b-material-flow.md`](evidence/2026-07-13-gate-0b-material-flow.md),
[`2026-07-13-gate-0b-serial-batch.md`](evidence/2026-07-13-gate-0b-serial-batch.md),
[`2026-07-13-gate-0b-transaction-variants.md`](evidence/2026-07-13-gate-0b-transaction-variants.md),
[`2026-07-13-gate-0b-performance.md`](evidence/2026-07-13-gate-0b-performance.md).

Gate 0B result: `PASS WITH REQUIRED FALLBACKS`. Native cross-owner
Batch/Serial validation is insufficient; ADR 0002 makes the mapping, guard and
composite index mandatory.

## Gate 0C — zero valuation та глобальний FIFO

- [x] приймання commission stock з нульовою оцінкою;
- [x] приймання consignment stock з нульовою оцінкою;
- [x] відсутність COGS для стороннього товару;
- [x] own/commission/consignment розміщені в окремих warehouses;
- [x] allocation до SI виконує глобальний FIFO між warehouses;
- [x] два конкурентні checkout не продають той самий lot/serial;
- [x] cancel/repost не пошкоджує майбутні SLE.

Gate 0C result: `PASS`. Evidence:
[`2026-07-13-gate-0c-global-fifo.md`](evidence/2026-07-13-gate-0c-global-fifo.md).

## Gate 0D — бухгалтерія

- [x] commission recognition JE;
- [x] consignment recognition JE;
- [x] debt JE після submit settlement report;
- [x] Payment Entry посилається на потрібний JE/report;
- [x] один report допускає кілька часткових PE;
- [x] один PE не оплачує кілька third-party reports;
- [x] foreign-currency partial payment і exchange difference;
- [x] cancel/reversal order;
- [x] adjustment у відкритому періоді для backdated economic event.

Gate 0D result: `PASS WITH REQUIRED APPLICATION GUARDS`. Standard JE, Payment
Ledger, Payment Entry та system-generated Exchange Gain/Loss JE підтверджені на
ERPNext v16. Один-report-per-PE, source links, economic/posting dates і
детерміноване створення PE забезпечує застосунок. Evidence:
[`2026-07-13-gate-0d-accounting.md`](evidence/2026-07-13-gate-0d-accounting.md).

## Gate 0E — POS saga

- [x] `POS Order` існує на test site;
- [x] змішаний кошик сплітується за legal entity та fiscal policy;
- [x] один payment plan коректно розподіляється між кількома SI;
- [x] timeout/retry не дублює документи;
- [x] частково проведений checkout має компенсаційний шлях;
- [x] окремі fiscal/non-fiscal print jobs;
- [x] повернення відновлює правильний lot/ownership state.

Gate 0E result: `PASS WITH REQUIRED EXTERNAL SAGA`. Поточний `erpnext_ua` POS
Order підтверджений як корінь checkout, але його single-SI schema не може бути
джерелом 1:N split state. Ідемпотентні routes, reservations, print jobs та
compensation належать цьому застосунку через POS adapter. Evidence:
[`2026-07-13-gate-0e-pos-saga.md`](evidence/2026-07-13-gate-0e-pos-saga.md).

## Gate 0F — ownership conversion

- [x] commission UAH, одна виплата;
- [x] commission foreign currency, дві виплати;
- [x] consignment UAH;
- [x] partial quantity return;
- [x] serialized return;
- [x] stock asset/COGS/payable після conversion звіряються з очікуванням.

Gate 0F result: `PASS WITH WAREHOUSE-VALUATION BOUNDARY`. Conversion uses a
zero-valued third-party issue plus a stock-updating Purchase Invoice into OWN;
partner return uses only the exact source issue. Inventory Dimension protects
quantity but does not create a valuation queue, so untracked own stock retains
standard warehouse-level FIFO. Evidence:
[`2026-07-13-gate-0f-ownership-conversion.md`](evidence/2026-07-13-gate-0f-ownership-conversion.md).

## Exit criteria

Stage 1 дозволено лише після PASS для 0A та письмових ADR для всіх FAIL/BLOCKED
у 0B–0F. Sales Invoice hooks не вмикаються, доки відповідний сценарій не має
integration test.

Stage 0 result: `PASS WITH DOCUMENTED PLATFORM BOUNDARIES`. Gate 0A закрито,
Gate 0B–0F мають evidence та ADR для всіх required fallbacks/boundaries. Stage 1
foundation дозволено; production hooks усе ще заборонені до їхніх окремих
integration tests.
