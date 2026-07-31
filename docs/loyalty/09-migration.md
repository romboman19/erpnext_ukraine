# Migration and cutover

1. Deploy code з feature OFF.
2. Виконати `bench --site <site> migrate`; перевірити DocTypes, custom fields та unique indexes.
3. Створити Scope/Location/Program/Card Type/Tiers; sample thresholds: 500, 10 000, 40 000, 80 000.
4. Опублікувати Program snapshot.
5. Завантажити `UA Loyalty Import Batch` як dry-run; усунути duplicate Customer/card conflicts.
6. Виконати opening import, запустити reconciliation та зберегти totals.
7. Увімкнути один test Scope/POS Cash Desk і пройти sale/return/fiscal UAT.
8. Після accounting/fiscal sign-off увімкнути production scope.

Історичні Sales Invoice автоматично не backfill-яться. Opening strategy задає cutover boundary; return старого продажу обробляється погодженим manual adjustment. Rollback вимикає feature та припиняє нові quote; проведені ledgers не видаляються.
