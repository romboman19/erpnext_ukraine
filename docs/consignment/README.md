# Модуль Consignment and Commission

Документація комісійно-консигнаційного домену. До `erpnext_ua` 0.9.0 домен
постачався окремим застосунком `erpnext_consignment_and_commission`
(release `1.1.0`, commit `5714aca`) і має власну історію рішень.

- [`adr/`](adr/) — архітектурні рішення. [ADR-0009](adr/0009-single-app-consolidation.md)
  фіксує перехід в один застосунок і замінює [ADR-0001](adr/0001-application-boundary.md).
- [`contracts/`](contracts/) — versioned API-контракти (POS v1).
- [`implementation-plan.md`](implementation-plan.md) — етапи розроблення домену.
- [`stage-1/`](stage-1/), [`stage-2/`](stage-2/), [`stage-3/`](stage-3/) —
  опис і докази приймання кожного етапу.
- [`spikes/`](spikes/) — Phase 0 прототипи та їх evidence.
- [`release/`](release/) — acceptance-звіти релізів.

Файли в `spikes/` і `*/evidence/` — це записи фактично виконаних прогонів. Вони
навмисно збережені дослівно і посилаються на старе ім'я застосунку: переписувати
докази заднім числом не можна. Актуальні команди — у
[`deployment/README.md`](../../deployment/README.md).
