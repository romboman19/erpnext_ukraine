# Runbook: технічні склади

Один склад належить рівно одному домену (ADR-001). Це найдорожча помилка конфігурації, бо виявляється вона вже під час продажу.

## Симптом

`WAREHOUSE_DOMAIN_CONFLICT` або `CC_WAREHOUSE_CONFLICT` при створенні `GSF Warehouse Binding`.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_list --kwargs '{"doctype":"GSF Warehouse Binding","fields":["warehouse","company","manager_app","warehouse_role"]}'
```

## Чого НЕ робити

Не «переприв'язувати» склад, який уже має рух запасу. Історія книги лишиться під старим доменом, і жоден звіт це не зведе.

## Виправлення

Створіть **новий** склад під потрібну роль і прив'яжіть його. Ролі:
- `GSF_OWN_POOL` — пул компанії, звідки береться запас;
- `GSF_SALE_STAGE` — lane, рівно один чек одночасно (ADR-006);
- `GSF_RETURN_QUARANTINE` — карантин трекінгових повернень (§19.3).

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_readiness
```
Блок «bound to two stock domains» має зникнути.

## Rollback / ескалація

Якщо склад уже змішав два домени — це `manual-review.md`: розділення потребує ручного рішення про те, чий запас там лежить.
