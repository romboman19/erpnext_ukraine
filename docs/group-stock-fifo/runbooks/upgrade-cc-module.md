# Runbook: оновлення комісійного модуля

GSF і CC — один репозиторій, один застосунок. Версійного контракту між ними немає (ADR-011 скасовано ревізією).

## Симптом

Після зміни CC падають GSF-тести або алокатор поводиться інакше.

## Безпечна діагностика

Ці команди нічого не змінюють.

```bash
python3 -m pytest -q erpnext_ua/consignment_and_commission/tests erpnext_ua/group_stock_fifo/tests
```

## Чого НЕ робити

Не форкати `allocate_global_fifo`. ADR-013: один аллокатор, два адаптери; копія розійдеться з оригіналом мовчки.

## Виправлення

GSF залежить від CC у трьох місцях: `services/allocation.py` (аллокатор і `SOURCE_METHOD_RELATIONSHIP_MODEL`, де є рядок `GSF_LAYER`), `services/candidates.py` (`CandidateQuery`, `preview_from_adapters`) і `CC Location` у перевірці складів. Зміна будь-якого з них — привід прогнати обидва набори тестів.

## Перевірка

Обидва набори тестів зелені; `test_reservation_domain.py` спеціально перевіряє, що комісійні методи не змінились.

## Rollback / ескалація

Якщо CC змінить сигнатуру аллокатора — правити адаптер GSF, а не копіювати аллокатор.
