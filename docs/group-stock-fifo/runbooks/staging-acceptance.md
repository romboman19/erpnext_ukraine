# Staging acceptance: scheduler, crash recovery and FIFO contention

Цей runbook закриває керовану частину Phase 8: живий scheduler/worker,
виконання TTL-expiry у черзі, rollback після аварійного завершення процесу та
конкурентні резервування одного Global FIFO scope. Він **не** замінює
production acceptance на анонімізованій копії реальних даних.

## Межі безпеки

- Сценарій дозволений лише на `fifoaccept.local`, `integration.local` або
  `postest.local`. Production site не можна додавати до allowlist.
- Site має бути окремим, без ключів ПРРО, платіжних, банківських, ecommerce та
  delivery-профілів. Перед увімкненням scheduler черга має бути порожньою.
- `tools/run_gsf_staging_load.sh` створює Company, склади, товар, Stock Entry й
  audit-документи з namespace Phase 0/3. Не запускати на копії production.
- Кожен `run-id` одноразовий. Повторне використання блокується, щоб старий
  idempotency evidence не маскував новий збій.

## Runtime image

UAT-контейнери мають встановлювати Python-залежності з `pyproject.toml` під час
збірки, а не вручну після recreate:

```bash
docker build \
  --build-arg UA_BASE_IMAGE=<approved-erpnext-v16-image> \
  -f deployment/Dockerfile.uat-runtime \
  -t erpnext-ua:0.16.0-uat-runtime .

UA_RUNTIME_IMAGE=erpnext-ua:0.16.0-uat-runtime docker compose \
  -f <base-compose.yml> \
  -f deployment/frappe-uat-runtime.override.yml \
  up -d backend worker scheduler
```

Перевірити до тесту:

```bash
docker exec <backend> bench --site <acceptance-site> list-apps
docker exec <backend> bench --site <acceptance-site> doctor
docker exec <backend> bench --site <acceptance-site> enable-scheduler
```

Очікується: лише `frappe`, `erpnext`, `erpnext_ua`; щонайменше один worker;
нуль queued jobs. Scheduler вмикається тільки на dedicated acceptance site.

## Прогін

Початковий smoke:

```bash
tools/run_gsf_staging_load.sh \
  <backend-container> <acceptance-site> smoke-YYYYMMDD-HHMM 4 50
```

Релізний прогін:

```bash
tools/run_gsf_staging_load.sh \
  <backend-container> <acceptance-site> release-YYYYMMDD-HHMM 8 100
```

Harness послідовно:

1. будує ізольовану три-ФОП fixture з 10 одиницями одного Item;
2. ставить прострочений резерв у реальну `long` queue і чекає статусу
   `EXPIRED` від worker;
3. робить резерв без commit, надсилає процесу `SIGKILL`, перевіряє rollback і
   повторне використання того самого scope lock;
4. запускає дві окремі DB-сесії, які одночасно забирають усі останні 10
   одиниць, і вимагає рівно одного переможця без oversell;
5. запускає окремі процеси, які одночасно reserve/release по одній одиниці;
6. агрегує latency, помилки, InnoDB deadlocks, heartbeat, repost queue та
   залишкові live allocations.

## Pass criteria

- scheduled expiry виконаний worker-ом не довше ніж за 45 секунд;
- аварійний процес не залишив allocation, reserved quantity або lock;
- у last-stock race рівно один процес отримав усі 10 одиниць, другий відмову;
- усі `workers × iterations` операцій успішні;
- p95 reserve/release не перевищує 5 секунд;
- після тесту `reserved_qty = 0`, live allocation = 0, queued repost = 0;
- scheduler active, heartbeat вкладається в application health window
  (`max(3 × scheduler tick, 15 хвилин)`), worker online.

Наявність InnoDB deadlock не є автоматичним fail, якщо bounded retry завершив
усі операції без витоку. Лічильник фіксується в evidence для порівняння між
релізами.

## Завершення

Зберегти повний stdout/stderr як release evidence. Після прогону:

```bash
docker exec <backend> bench --site <acceptance-site> doctor
docker exec <backend> bench --site <acceptance-site> execute \
  erpnext_ua.group_stock_fifo.integration_tests.phase_3_fixture.teardown \
  --kwargs '{"confirm_write":"DROP_GSF_PHASE_3"}'
docker exec <backend> bench --site <acceptance-site> disable-scheduler
```

Якщо harness зупинився після вже закоміченого резерву, спершу зберегти fail
evidence, а потім перевести лише allocation цього `run-id` у terminal state:

```bash
docker exec <backend> bench --site <acceptance-site> execute \
  erpnext_ua.group_stock_fifo.integration_tests.phase_8_load_worker.cleanup_failed_run \
  --kwargs '{"confirm_write":"RELEASE_FAILED_GSF_PHASE_8_RUN","run_id":"<run-id>"}'
```

## Відомі image gates перед production

Ці проблеми не змінюють результат GSF-тесту, але блокують затвердження
production image:

1. Базовий UAT image містить legacy-копії `consignment_and_commission` та
   `ukrainian_integrations`, через що Frappe попереджає про дублікати Module.
   У production image має лишитися один app `erpnext_ua` і один власник
   кожного модуля.
2. Поточний dependency graph суперечливий: Frappe 16.26.3 декларує
   `Click ~= 8.3.1`, тоді як bundled Flow → LiteLLM фіксує `click == 8.1.8`.
   Оновлювати Click поверх image без узгодження Flow/LiteLLM не можна;
   production build має проходити `pip check` без конфлікту.
3. Базовий image не містив усіх залежностей `erpnext_ua`; runtime layer вище
   це виправляє для UAT. Production Dockerfile має робити те саме від
   зафіксованого digest і проходити clean rebuild.

Після закриття image gates останнім обов'язковим кроком залишається §43:
acceptance на анонімізованій копії реальних залишків, резервів, рахунків і
Multi-FOP налаштувань із підписом власника та бухгалтера.
