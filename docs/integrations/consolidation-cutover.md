# Виведення `ukrainian_integrations` з бою

Застосунок `ukrainian_integrations` влито в `erpnext_ua`. Дані інтеграцій —
DocType, документи, налаштування, кастомні поля — не змінюються. Змінюється
**власник**: модулі `Ukrainian Integrations` і `Ecommerce` тепер належать
`erpnext_ua`, а шляхи патчів у `Patch Log` мають новий префікс.

Цей документ описує перехід на сайті, де старий застосунок уже встановлений.
Для чистого встановлення нічого з цього не потрібно: `bench install-app
erpnext_ua` ставить усе одразу.

## Що станеться, якщо просто зробити `git pull` і `bench migrate`

Нічого доброго:

- `bench migrate` впаде, бо `sites/apps.txt` і `tabInstalled Application`
  згадують застосунок, чийого коду вже немає;
- якщо код лишити на місці — два застосунки зареєструють ті самі хуки двічі:
  подвійні планувальники, подвійні `doctype_js`, подвійна фіскалізація;
- `Module Def "Ecommerce".app_name` залишиться `ukrainian_integrations`, і патч
  `register_ecommerce_module_and_sync_mapping` кине
  `Module Ecommerce is already owned by app ukrainian_integrations`.

## Порядок переходу

Кроки 1–3 виконати на **копії** бази й лише потім на бою.

### 1. Резервна копія

```bash
bench --site <site> backup --with-files
```

Переконайтеся, що копія читається, перш ніж рухатися далі. Крок 4 не має
безпечного авто-відкату: `Patch Log` перепишеться.

### 2. Зняти застосунок з обліку сайту, не видаляючи дані

```bash
bench --site <site> remove-from-installed-apps ukrainian_integrations
```

Саме ця команда, і **тільки** вона. `bench uninstall-app` видаляє DocType разом
із документами — тобто всі ТТН, платежі, замовлення маркетплейсів і журнали.

### 3. Прибрати старий застосунок з bench

```bash
bench --site <site> clear-cache
sed -i '/^ukrainian_integrations$/d' sites/apps.txt
./env/bin/pip uninstall -y ukrainian_integrations
rm -rf apps/ukrainian_integrations
```

### 4. Оновити `erpnext_ua` і мігрувати

```bash
cd apps/erpnext_ua && git pull && cd ../..
./env/bin/pip install -e apps/erpnext_ua
bench --site <site> migrate
```

Під час міграції першим у `pre_model_sync` іде
`erpnext_ua.patches.consolidation.rename_integration_patch_log`. Він:

- перейменовує п'ять записів `Patch Log` на нові шляхи, щоб уже виконані патчі
  не запускалися вдруге;
- переводить `Module Def.app_name` для `Ukrainian Integrations` та `Ecommerce`
  на `erpnext_ua`;
- переводить `Workspace.app` для робочих просторів інтеграцій.

Порядок у `patches.txt` тут — контракт: цей патч мусить іти **перед**
`register_ecommerce_module_and_sync_mapping`, інакше той побачить чужого
власника модуля й зупинить міграцію.

### 5. Перевірка

```bash
bench --site <site> list-apps
bench --site <site> execute erpnext_ua.integrations.diagnostics.run_installation_checks
bench --site <site> migrate
```

Очікується:

- `list-apps` показує `erpnext_ua` і не показує `ukrainian_integrations`;
- діагностика проходить без помилок;
- повторна міграція — no-op (жодного патчу не виконано вдруге);
- у `Patch Log` немає жодного запису з префіксом `ukrainian_integrations.`:

```bash
bench --site <site> mariadb -e "SELECT patch FROM \`tabPatch Log\` WHERE patch LIKE 'ukrainian_integrations%'"
```

Порожній результат — те, що потрібно.

Далі перевірити вручну, бо це зовнішні системи:

- у планувальнику один heartbeat, а не два (`System Health Report`);
- Нова Пошта / Укрпошта / Rozetka: синхронізація статусів ТТН;
- банківський імпорт Monobank/PrivatBank не створює дублікатів (унікальний ключ
  той самий, `ua_integration_key`);
- Telegram-канал `Notification` надсилає повідомлення;
- ocStore / Prom.ua обмін файлами й замовленнями.

## Відкат

До кроку 4 включно відкат — це відновлення резервної копії з кроку 1 плюс
повернення старого застосунку в `apps/` та `apps.txt`.

Після кроку 4 відкат виконується тільки через відновлення копії: `Patch Log`
уже перезаписаний, і повернення старого застосунку без відновлення бази змусить
Frappe вважати п'ять патчів невиконаними.

## Що змінилося для розробки

| Було | Стало |
| --- | --- |
| `ukrainian_integrations.utils.*` | `erpnext_ua.integrations.utils.*` |
| `ukrainian_integrations.payments.*` | `erpnext_ua.integrations.payments.*` |
| `ukrainian_integrations.shipment.*` | `erpnext_ua.integrations.shipment.*` |
| `ukrainian_integrations.pbx_sms.*` | `erpnext_ua.integrations.pbx_sms.*` |
| `ukrainian_integrations.communication.*` | `erpnext_ua.integrations.communication.*` |
| `ukrainian_integrations.customer_identification.*` | `erpnext_ua.integrations.customer_identification.*` |
| `ukrainian_integrations.monitoring.*` | `erpnext_ua.integrations.monitoring.*` |
| `ukrainian_integrations.migrations` | `erpnext_ua.integrations.migrations` |
| `ukrainian_integrations.diagnostics` | `erpnext_ua.integrations.diagnostics` |
| `ukrainian_integrations.ecommerce.*` | `erpnext_ua.ecommerce.*` |
| `ukrainian_integrations.patches.*` | `erpnext_ua.patches.*` |
| `/assets/ukrainian_integrations/...` | `/assets/erpnext_ua/...` |

Модулі Frappe (`Ukrainian Integrations`, `Ecommerce`) назви не змінили, тому
`DocType.module` і посилання в документах лишилися незмінними.
