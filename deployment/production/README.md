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
commit зовнішнього `print_designer`. Код `erpnext_ua` завжди копіюється з
чистого git checkout, а його commit записується в OCI label і
`ERPNEXT_UA_IMAGE_COMMIT`.

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
bench virtualenv та повторно запускає runtime validator. CI виконує ту саму
команду. Офіційний Frappe Docker також радить передавати custom app manifest як
BuildKit secret; цей repo не передає credentials узагалі, бо всі build inputs
публічні й зафіксовані commit/digest.

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
