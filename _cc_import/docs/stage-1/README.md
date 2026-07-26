# Stage 1 — foundation

## Мета

Створити production-shaped master data без активації stock, Sales Invoice,
Payment Entry або POS hooks. ERPNext залишається джерелом істини для Company,
Supplier, Warehouse, Account і Currency.

## Foundation slice 1

- [x] `CC Settings` з feature gate та bounded allocation parameters;
- [x] `CC Location` з трьома різними leaf warehouses однієї Company;
- [x] allow-listed legal entity types `Company` і optional `FOP Profile`;
- [x] `CC Partner Profile`, зв'язаний з одним ERPNext Supplier;
- [x] `CC Contract` з моделлю, строком, валютою, settlement і fiscal policy;
- [x] заборона overlapping Active Contract для одного partner/location/model;
- [x] `CC Account Mapping` із semantic accounts і root-type validation;
- [x] ролі manager/user/auditor та Workspace `Commission Trade`;
- [x] Stage 1 readiness checks;
- [x] self-cleaning Frappe integration smoke на paused restore site;
- [x] повний Frappe integration CI у GitHub Actions;
- [x] initial master records для test site з вимкненим feature gate.

## Межі

- `CC Settings.enabled` за замовчуванням вимкнено;
- стандартні ERPNext transaction hooks не зареєстровані;
- `FOP Profile` читається через Frappe DocType API, без імпорту Python-модулів
  `erpnext_ua`;
- Warehouse, Account і Supplier не дублюються в застосунку;
- зміна ownership, stock receipt, allocation і settlement належать наступним
  stages.

## Exit criteria

Stage 2 дозволено після успішного `bench migrate` на `postest.local`, зеленого
foundation smoke/readiness, повного Frappe integration CI та погодження
початкових Location/Partner/Contract/Account Mapping records. Production hooks
до цього залишаються заборонені.

Evidence:

- [`evidence/2026-07-13-foundation-slice-1.md`](evidence/2026-07-13-foundation-slice-1.md);
- [`evidence/2026-07-13-foundation-slice-2.md`](evidence/2026-07-13-foundation-slice-2.md).

Test-only bootstrap: [`test-site-bootstrap.md`](test-site-bootstrap.md).
