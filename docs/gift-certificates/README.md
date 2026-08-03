# UA Gift Certificates

Модуль входить до `erpnext_ua` і моделює сертифікат як окреме фінансове право: HMAC-ідентичність, зашифрований token, append-only subledger, reservation, item-level allocation та paid/promotional funding.

Production enable заборонений, доки `UA Gift Certificate Settings.stage0_status != Passed` або readiness має blocking checks. Після install/migrate feature flags вимкнені.

Підтриманий V1-профіль: UAH, `Prepaid Payment`, non-VAT profile із документованим compliance allow, POS-UA, same-entity redemption; cross-entity subledger дозволяється лише з активним Settlement Profile. VAT/deferred-discount/ecommerce залишаються fail-closed.

Операційні документи: [Stage 0](00-stage0-report.md), [архітектура](02-architecture.md), [тести](10-testing.md), [міграція](11-migration-cutover.md), [reconciliation](12-reconciliation-runbook.md), [каса](13-cashier-guide.md), [інциденти](15-incident-recovery.md).
