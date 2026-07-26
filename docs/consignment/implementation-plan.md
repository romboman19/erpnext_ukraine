# План реалізації

План завершено 2026-07-14. Історичні spike/slice evidence залишаються у
відповідних `docs/stage-*` каталогах; поточний релізний стан описано нижче.

## Stage 0 — technical spike — завершено

Підтверджено Inventory Dimension, zero valuation, global FIFO/locking, POS
split, foreign-currency JE/PE, cancellation та ownership conversion. Межі
платформи зафіксовано в ADR 0002–0006.

## Stage 1 — foundation — завершено

Реалізовано Settings, ролі, Workspace, locations, legal-entity adapter,
Partner Profile, Contract, Account Mapping, diagnostics, fixtures і clean-site
Frappe CI. Feature gate за замовчуванням вимкнений.

## Stage 2 — receipt and stock — завершено

Реалізовано всі чотири методи приймання, immutable Stock Lot, production
Inventory Dimension, zero-valued third-party receipt, stock-updating OWN
Purchase Invoice, Serial/Batch ownership, exact stock balance, partner return,
ownership conversion, cascade cancellation й потрібні database indexes.

## Stage 3 — sale and POS — завершено

Реалізовано єдиний FIFO, atomic reservation/TTL/idempotency, managed Sales
Invoice, immutable sale allocations, exact Serial/Batch, split POS route saga,
payment state, print queue, retries, compensation та versioned permission-aware
API.

## Stage 4 — settlement and payments — завершено

Реалізовано recognition posting, Settlement Report, Supplier debt JE, строки,
partial Payment Entry, multi-currency outstanding, exchange difference,
cancellation guards і partner balance report.

## Stage 5 — pricing, returns and corrections — завершено

Реалізовано versioned price snapshots, commission/consignment calculations,
returns до/після settlement, settlement adjustment, closed-period posting date,
foreign currency та third-party → OWN conversion.

## Stage 6 — unsold goods and integrations — завершено

Реалізовано точне повернення непроданого залишку партнеру, provider-neutral POS
adapter boundary, persistent print/manual-review queue та операційні reports.
Конкретні fiscal/communication providers лишаються deployment configuration.

## Stage 7 — hardening and rollout — завершено для release candidate

Завершено unit/domain coverage, clean-site Frappe integration regression,
конкурентний reservation proof, security/company-scope checks, financial
integrity audit, schema/index diagnostics, clean install, повторну idempotent
migration і deployment/rollback runbook.

Production rollout залишається окремою керованою операцією: backup, staging
rehearsal на копії конфігурації, бухгалтерське acceptance, provider acceptance,
пілот однієї Company/Location і моніторинг після активації.
