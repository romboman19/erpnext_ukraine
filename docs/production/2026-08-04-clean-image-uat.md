# Clean production image UAT — 2026-08-04

Вердикт: **PASS для immutable image, подвійної міграції та code-controlled
Global FIFO gates**. Це не дозвіл на production go-live: §43 acceptance на
анонімізованій копії реальних залишків, резервів, рахунків і Multi-FOP
налаштувань та підписи власника/бухгалтера лишаються обов'язковими.

## Перевірений runtime

| Параметр | Значення |
| --- | --- |
| PR | `#25`, `agent/p0-production-image-contract` |
| фінальний image source | `4eb8760838e623079e0f232c88b4b923bff01aa8` |
| локальний image | `erpnext-ua:uat-4eb8760` |
| image ID | `sha256:8a56705fde34547f5f6e63e5f49338f1c64347aeebeacea0f5e2a8eb4dc17053` |
| Frappe / ERPNext | 16.26.3 / 16.26.2 |
| ERPNext Ukraine | 0.16.0 |
| Print Designer | 1.6.5, commit `e4faa7e48118b4bdfea8a522368e4ea62e6cc210` |
| Chromium | 133.0.6943.35, SHA-256 `4e5fa57a0338b28e35e16693a15c985c377eae58e54321f5642ff5adc5d9ae87` |
| Chromium runtime package | `libatk-bridge2.0-0=2.46.0-5` |
| UAT sites | `uat.local`, dedicated allowlisted `fifoaccept.local` |

Усі `backend`, `worker`, `scheduler`, `frontend` і `websocket` контейнери
мають той самий OCI revision. У них немає bind mount checkout-коду: змонтовані
лише `sites` і `logs` volumes. `headless_shell --version` у кожному контейнері
повернув `Google Chrome for Testing 133.0.6943.35`.

## Cutover і міграція

Перед зміною UAT створено database/files backups обох sites. Спільний
volume `sites/apps.txt` містив старі назви `ukrainian_integrations`,
`erpnext_consignment_and_commission` і `flow`, хоча вони вже не були
встановлені на sites. Файл резервно скопійовано й приведено до exact set:

```text
erpnext
erpnext_ua
frappe
print_designer
```

На обох sites встановлено Print Designer 1.6.5 і виконано по два послідовні
`migrate`. Після recreate фінального image подвійний migrate повторено. Для
кожного site:

- runtime/site image contract — PASS;
- `pip check` — без конфліктів;
- `erpnext_ua.install.assert_modules_registered` — 14 модулів;
- exact installed apps — Frappe, ERPNext, ERPNext Ukraine, Print Designer;
- Global FIFO integrity — `critical_count=0`, findings відсутні.

Legacy apps прибрані тільки з app registry; `uninstall-app` не
використовувався, тому consolidation cutover не видаляв бізнес-дані.

## Global FIFO acceptance

Повний staging harness виконував scheduled expiry через реальний worker,
rollback після `SIGKILL`, гонку двох DB-сесій за останні 10 одиниць і
паралельні reserve/release. Після повних load-прогонів CI виявив синхронні
повтори deadlock victims у `reserve()`. На revision `4eb8760` додано bounded
deterministic jitter без зміни FIFO-порядку або acceptance gates, після чого
інтегрований smoke повністю повторено на image з цим runtime-кодом.

| Прогін | Результат | p95 | max | Throughput | Deadlocks | Залишки |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| clean image smoke 4 × 50 | 200/200 PASS | 0.649350 с | 0.905918 с | 16.318 ops/s | 0 | 0 live, 0 reserved, 0 repost |
| clean image release 8 × 100 | 800/800 PASS | 0.668038 с | 15.326335 с | 14.519 ops/s | 0 | 0 live, 0 reserved, 0 repost |
| final revision integrated smoke 4 × 20 | 80/80 PASS | 0.704081 с | 0.843549 с | 10.716 ops/s | 0 | 0 live, 0 reserved, 0 repost |

В обох зарахованих прогонах scheduled expiry, crash recovery, last-stock
winner/loser, p95 gate, worker і scheduler heartbeat пройшли. Перший
4 × 50 запуск мав 200/200 і p95 0.391032 с, але був чесно відхилений через
відсутній heartbeat одразу після recreate; run-id очищено, після реального
scheduler tick повторний прогін пройшов. Перший smoke на `4eb8760` мав 80/80,
p95 0.863310 с, 0 deadlocks і 0 leaks, але теж був відхилений: він стартував
до першого heartbeat після recreate. Після реального scheduler job повторний
run `final-4eb8760-r2` пройшов усі gates і підтвердив scheduled expiry, crash
rollback, рівно одного переможця гонки за останні 10 одиниць та свіжий
heartbeat.

## Дефекти, знайдені цим етапом

1. Site volume перекриває baked `sites/apps.txt`, тому clean image сам по собі
   не прибирає старий bench registry. Cutover тепер вимагає exact-set repair і
   site validator.
2. Print Designer завантажував Chromium під час `install-app`, через що новий
   worker залежав від мережі й mutable container layer. Chromium зафіксовано
   версією/SHA-256 і перенесено в image build.
3. Перша offline-перевірка baked Chromium знайшла відсутній
   `libatk-bridge-2.0.so.0`. Debian package зафіксовано точною версією, а
   validator тепер запускає `headless_shell --version`, тому самої наявності
   executable недостатньо.
4. Teardown fixture поставив 1203 стандартні `delete_dynamic_links` jobs.
   Scheduler залишався вимкненим, штатний worker спорожнив default queue до
   нуля; додаткові workers не запускалися. Фінальний smoke створив ще 107
   таких cleanup jobs, а smoke на `4eb8760` — 184; усі вони дреновані до нуля
   тим самим worker.
5. Clean-site CI зафіксував три вичерпані `ALLOCATION_CONFLICT` при 15 InnoDB
   deadlocks: потерпілі транзакції повторювалися синхронно без паузи. Retry-path
   отримав bounded exponential window із deterministic key-specific jitter;
   повторний clean-site і фінальний UAT завершилися без помилок і deadlocks.

## Безпечний фінальний стан і межі висновку

Після acceptance fixture видалено: 0 layers, allocations, balances,
scope locks, reallocations, checkouts і stock counts; GSF feature gate
вимкнений. Обидва UAT sites залишено з `maintenance_mode=1`, scheduler
disabled, worker online і порожніми queues.

Production контейнери не змінювалися. До go-live ще потрібні:

- §43 acceptance на анонімізованій production-копії;
- бухгалтерський, фіскальний/provider та операційний sign-off;
- maintenance window з новим backup і перевіреним rollback;
- відновлення/перевірка production queue workers та запасу диска перед
  cutover;
- окреме рішення щодо Flow після усунення конфлікту Flow → LiteLLM → Click.
