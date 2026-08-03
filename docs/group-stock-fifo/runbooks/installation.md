# Runbook: встановлення

GSF — модуль усередині `erpnext_ua`, не окремий застосунок. Окремо він не встановлюється й не видаляється.

## Симптом

Після `bench migrate` DocType `GSF *` не з'явились, або з'явилась частина.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> list-apps
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_count --kwargs '{"doctype":"DocType","filters":{"module":"Group Stock FIFO"}}'
```

## Чого НЕ робити

Не створювати DocType вручну через desk — вони мають прийти з міграції, інакше наступний migrate їх перезапише або видалить.

## Виправлення

Прогнати міграцію **двічі**: перший прохід створює Module Def, другий синкає DocType.

```bash
docker exec frappe-test-backend-1 bench --site <site> migrate
docker exec frappe-test-backend-1 bench --site <site> migrate
```

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_readiness
```
Очікується `blocked` зі списком налаштувань, які ще не зроблені — це нормальний стан свіжої установки, бо feature gate закритий (§44).

## Rollback / ескалація

Якщо після двох проходів DocType усе ще немає — дивіться помилку самої міграції у `bench --site <site> console`; GSF не має власного інсталятора, який міг би її обійти.
