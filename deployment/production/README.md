# Clean production image

Цей профіль збирає один незмінний Frappe/ERPNext v16 image з чотирма apps:

- `frappe` 16.26.3;
- `erpnext` 16.26.2;
- `erpnext_ua` 0.16.0, який містить Global FIFO, Multi-FOP, POS-UA,
  Consignment and Commission та українські інтеграції;
- `print_designer` 1.6.5.

Окремі `ukrainian_integrations` і `erpnext_consignment_and_commission` у цей
образ не входять: їхній код і дані вже консолідовані в `erpnext_ua`. Flow також
не входить до clean profile. Актуальний Flow залежить від LiteLLM 1.83.7, який
фіксує `click==8.1.8`, а Frappe 16.26.3 вимагає `Click~=8.3.1`. Приховувати цей
конфлікт через `--no-deps` або вимкнення `pip check` заборонено.

## Незмінні входи

[`source-lock.json`](source-lock.json) фіксує digest базового ERPNext image та
commit зовнішнього `print_designer`. Там само зафіксовано URL, версію і SHA-256
Chromium headless shell, потрібного Print Designer: browser завантажується під
час build і має бути executable у кожному backend/worker image, а не
довантажуватися після запуску. Код `erpnext_ua` завжди копіюється з чистого git
checkout, а його commit записується в OCI label і `ERPNEXT_UA_IMAGE_COMMIT`.

Зміна будь-якої версії або digest вимагає одночасно оновити
[`image-contract.json`](image-contract.json), source lock, пройти CI і повторити
UAT acceptance. Плаваючі `develop`, `main` або image tags без digest у
production build не допускаються.

## Збірка

На чистому checkout:

```bash
tools/build_production_image.sh registry.example/erpnext-ua:0.16.0-<commit>
```

Скрипт перевіряє source contract, збирає image, запускає `pip check` саме в
bench virtualenv, наявність зафіксованого Chromium та повторно запускає runtime
validator. CI виконує ту саму команду. Офіційний Frappe Docker також радить
передавати custom app manifest як BuildKit secret; цей repo не передає
credentials узагалі, бо всі build inputs публічні й зафіксовані commit/digest.

Для UAT цей image підставляється через `frappe-uat-runtime.override.yml`, а
[`frappe-uat-clean-image.override.yml`](../frappe-uat-clean-image.override.yml)
прибирає checkout bind mount із backend/worker/scheduler. Без другого override
тест перевіряв би host source, а не код, запечений в image.

## PRRO signer

[`prro-signer.override.yml`](prro-signer.override.yml) додає
`erpnext_ukraine_prro_signer` у той самий compose project. Signer не має
опублікованих портів, працює read-only без Linux capabilities і доступний
backend/worker як `http://prro-signer:8080`. Образ збирається з тегу signer-а
і підставляється тільки за digest:

```bash
git clone --branch v0.2.1 https://github.com/romboman19/erpnext_ukraine_prro_signer
docker build -t registry.example/erpnext-ukraine-prro-signer:0.2.1 erpnext_ukraine_prro_signer
docker push registry.example/erpnext-ukraine-prro-signer:0.2.1
# у .env compose project (не в git):
# PRRO_SIGNER_IMAGE=registry.example/erpnext-ukraine-prro-signer:0.2.1@sha256:<digest>
# PRRO_SIGNER_API_KEY=<openssl rand -hex 32>
docker compose -f compose.yaml <image overrides> \
  -f /path/to/erpnext_ukraine/deployment/production/prro-signer.override.yml up -d
```

Після старту перевірте з backend-контейнера `curl -fsS http://prro-signer:8080/health`
і переконайтеся, що на host-і порт 8080 не слухає. Signer потребує вихідного
HTTPS до TSP КНЕДП із сертифіката; якщо egress обмежено allowlist-ом, додайте
TSP-адресу вашого КНЕДП.

## Cutover на копії production site

Команди нижче спочатку виконуються тільки на UAT-копії з перевіреним backup.
Site лишається в maintenance mode, scheduler і workers не запускаються до
завершення двох міграцій.

```bash
bench --site <site> backup --with-files
bench --site <site> remove-from-installed-apps ukrainian_integrations
bench --site <site> remove-from-installed-apps erpnext_consignment_and_commission
bench --site <site> remove-from-installed-apps flow
bench --site <site> migrate
bench --site <site> migrate
/usr/local/bin/validate-production-image \
  --bench-root /home/frappe/frappe-bench \
  --contract /opt/frappe/production/image-contract.json \
  --site <site>
```

Використовується саме `remove-from-installed-apps`, а не `uninstall-app`:
останній видаляє DocType та бізнес-дані. Flow знімається з обліку тільки після
read-only підтвердження, що немає Agent, Trigger, Run, Session, Provider, Model
або Knowledge Base. Наявні seed Tool не є ознакою використання.

Після міграцій обов'язково перевірити:

- `bench --site <site> list-apps --format json` містить рівно чотири apps;
- `bench --site <site> doctor` бачить scheduler і workers;
- `env/bin/python -m pip check` не має помилок;
- `chromium/chrome-linux/headless_shell` є executable без runtime-download;
- повторний `migrate` є no-op;
- `erpnext_ua.install.assert_modules_registered` проходить;
- GSF/CC diagnostics, FIFO last-stock race, POS sale/return і fiscal outbox
  проходять на анонімізованій копії;
- `docker system df`, filesystem free space та Redis persistence мають запас і
  не містять `MISCONF`/`stop-writes-on-bgsave-error`.

## Rollback

До відкриття site користувачам rollback — повернути попередній image і
відновити database/files backup. Не додавати legacy apps назад до нового image
поверх уже виконаних consolidation patches. Якщо пілот створив операції,
спочатку вимкнути GSF/CC feature gates і виконати контрольоване сторнування за
основним production runbook.
