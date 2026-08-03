# Phase 8 staging acceptance — 2026-08-04

Вердикт: **PASS для code-controlled GSF staging gates**. Це не production
go-live: §43 на анонімізованій копії реальних даних і production image gates
лишаються обов'язковими.

## Середовище

| Параметр | Значення |
| --- | --- |
| site | dedicated allowlisted `fifoaccept.local`, без provider profiles/ключів |
| source | `agent/p0-gsf-staging-acceptance`, commit `138e91c` |
| imported app | `erpnext_ua.__version__ = 0.16.0` |
| Frappe / ERPNext | 16.26.3 / 16.26.2 |
| runtime | окремі backend, scheduler і worker; один worker слухає short/default/long |
| queue before run | 0 |
| scheduler | active; application heartbeat window 900 секунд |

`bench list-apps` показував metadata `erpnext_ua 0.15.0`, бо UAT image містив
старий app root, а bind mount замінював лише Python package. Імпортований код
був 0.16.0. Такий metadata/source drift прийнятний лише для цього UAT і
зафіксований як production image blocker.

## Обов'язкові гейти

| Gate | Результат |
| --- | --- |
| scheduled expiry | PASS: immediately-due allocation виконаний реальним `long` worker, статус `EXPIRED`, витоку немає |
| process crash | PASS: `SIGKILL` після reserve до commit; allocation і reserved qty відкотилися, scope одразу повторно використано |
| §37.7 last-stock race | PASS: дві DB-сесії просили всі 10 одиниць; рівно один winner на 10, loser отримав `INSUFFICIENT_GLOBAL_STOCK`; після release 0 live/0 reserved |
| contention smoke | PASS: 4 × 50 = 200/200, p95 0.598164 с, max 0.812987 с, 15.614 ops/s |
| release load | PASS: 8 × 100 = 800/800, p95 0.961623 с, max 4.703913 с, 14.078 ops/s |
| DB / queues after load | 0 InnoDB deadlocks observed, 0 errors, 0 live allocations, reserved qty 0, 0 queued reposts |
| scheduler/worker after load | active/recent heartbeat, worker online |

Після додавання last-stock gate окремий інтегрований smoke 4 × 20 також
пройшов: 80/80, p95 0.595421 с, max 0.692014 с, 16.573 ops/s, усі cleanup
checks зелені.

## Дефекти, які знайшов staging

1. Site creation у цьому UAT image створив MariaDB user лише для IP backend,
   тому worker/scheduler не могли виконувати jobs. Grant обмежено UAT
   Docker-підмережею, credential ротовано. Production automation має
   перевіряти DB login з backend, worker і scheduler, а не тільки `bench
   doctor` у backend.
2. Перший contention дав 191/200 і три live holds; наступний 177/200 і чотири
   live holds. MariaDB повертала error 1020, бо consistent-read snapshot
   створювався до scope lock, а terminal transitions не входили через той
   самий lock order. Виправлення: commit `307267b` серіалізував terminal
   transitions, commit `3375bed` відокремив read-only preflight від write
   transaction. Після цього 200/200 та 800/800 зелені.
3. Harness спочатку використовував відсутній у slim image executable `kill` і
   порівнював `Decimal("0.0")` як текст. Обидві помилки виправлені; cleanup
   кожного невдалого run підтверджував 0 live і 0 reserved перед повтором.

## Що ще блокує production

- clean production image без legacy-дублікатів модулів і без metadata/source
  drift;
- узгоджений dependency graph Frappe/Flow/LiteLLM/Click і зелений `pip check`;
- acceptance §43 на анонімізованій копії реальних залишків, відкритих
  резервів, рахунків і Multi-FOP налаштувань;
- бухгалтерський, фіскальний/provider та операційний sign-off пілота.

Відтворення й критерії: [staging-acceptance runbook](../../runbooks/staging-acceptance.md).
