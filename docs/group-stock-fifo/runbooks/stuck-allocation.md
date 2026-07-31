# Runbook: зависла allocation

Allocation тримає запас. Зависла allocation тримає його ні для кого.

## Симптом

`INSUFFICIENT_GLOBAL_STOCK` при тому, що запас фізично є.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```

## Чого НЕ робити

Не зменшувати `reserved_qty_cache` руками. Це одне число, спільне для всіх allocation на позицію: зменшите двічі — віддасте чужий запас (пастка №7 у HANDOFF).

## Виправлення

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.services.allocations.expire_due_allocations
```
Якщо allocation не прострочена, а просто покинута — `allocation_release` через API.

## Перевірка

```bash
docker exec frappe-test-backend-1 bench --site <site> execute erpnext_ua.group_stock_fifo.api.diagnostics_integrity
```
Попередження `ALLOCATION_EXPIRED` мають зникнути.

## Rollback / ескалація

Якщо прострочені allocation накопичуються — не працює scheduler; див. `valuation-divergence.md`.
