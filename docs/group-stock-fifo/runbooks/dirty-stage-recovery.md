# Runbook: брудна stage-lane

Lane стає `DIRTY`, коли після чека в ній лишився запас. Автоматично її **ніколи** не чистять (§44).

## Симптом

`STAGE_LANE_DIRTY`; каса не може почати чек.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
docker exec frappe-test-backend-1 bench --site <site> execute frappe.client.get_list --kwargs '{"doctype":"GSF Staging Lane","filters":{"status":"DIRTY"},"fields":["name","warehouse","dirty_reason","current_checkout"]}'
```

## Чого НЕ робити

Не переводити lane у `AVAILABLE` вручну, доки в ній є запас. Наступний чек продасть цей запас за чужою вартістю — рівно те, що показав гейт 0c.

## Виправлення

1. Знайдіть, що саме лежить у складі lane і під яким шаром.
2. Знайдіть checkout, який її тримав, і його reallocation.
3. Якщо продажу не було — `checkout_abort`: компенсація поверне запас у пули.
4. Якщо продаж був, а запас лишився — це розбіжність §16.4, ескалюйте.

## Перевірка

Lane `AVAILABLE`, `Bin` по її складу порожній, `diagnostics_integrity` без `STAGE_LANE_DIRTY`.

## Rollback / ескалація

Друга lane для тієї ж компанії розблокує касу, поки перша розбирається (ADR-006: пул lane саме для цього).
