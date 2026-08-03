# Runbook: оновлення ERPNext

GSF спирається на дві поведінки платформи, які апгрейд може змінити: `Inventory Dimension` і `FIFOValuation`.

## Симптом

Після оновлення падає preflight, або зникли поля виміру.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> version
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```

## Чого НЕ робити

Не оновлювати ERPNext із увімкненим гейтом на робочому сайті без прогону на копії.

## Виправлення

```bash
docker exec frappe-test-backend-1 bench --site <site> migrate
```
Патч ADR-002 сам перевірить схему й впаде, якщо поля не на місці. Це і є сигнал.

## Перевірка

Preflight читає `Stock Ledger Entry.stock_queue` і проганяє його через `erpnext.stock.valuation.FIFOValuation`. Якщо ERPNext змінить формат черги або клас — впаде саме тут, і це треба перевірити на копії до оновлення продакшна.

## Rollback / ескалація

Мажорна версія ERPNext ≠ 16 блокується readiness свідомо: сумісність не перевірялась.
