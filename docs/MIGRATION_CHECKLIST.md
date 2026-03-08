# Migration Checklist: hunter_integrations_repo -> erpnext_ukrainian_integrations

## 1) Shipment
- [x] Nova Poshta: базовий client + tracking + scheduler sync
- [x] Ukrposhta: базовий client + tracking + scheduler sync
- [ ] Перенести повний create-TTN flow (NP)
- [ ] Перенести повний create-shipment flow (UP)
- [ ] Перенести customer delivery profiles (NP/UP)
- [ ] Перенести UI кнопки/діалоги Sales Invoice

## 2) PBX/SMS
- [x] TurboSMS: send_sms + send_sms_to_customer
- [ ] Перенести sender profiles + test-send settings UI
- [ ] Перенести TurboSMS Log parity (якщо потрібно окремо від unified log)
- [ ] Додати VitalPBX інтеграцію (MVP)

## 3) Payments
- [x] Privat POS: gateway client + sale service
- [x] LiqPay: signed initiate + callback
- [x] PrivatBank statements fetch/import
- [x] Monobank statements fetch/import
- [x] Unified bank import orchestrator
- [ ] Поля write-back у SI/POS Invoice (стабільна схема)
- [ ] Payment Entry reconciliation flow

## 4) Ecommerce
- [x] Core contracts/registry/orchestrator
- [x] PromUA provider template
- [ ] Реалізувати PromUA order pull
- [ ] Реалізувати PromUA stock push
- [ ] Перенести Shop-Express реальну логіку
- [ ] Додати Rozetka provider (template + MVP)

## 5) Platform/Quality
- [x] CONTRIBUTING + basic CI (py_compile)
- [x] Unified Hunter Integration Log doctype
- [ ] install/uninstall hooks (реальна логіка)
- [ ] config/desktop workspace icons + pages
- [ ] smoke tests (API/scheduler/critical flows)
- [ ] release playbook scripts (staging/prod/rollback)

## Priority Plan

### Sprint A (найближчий)
1. PromUA real API (orders + stock)
2. NP/UP create shipment/TTN parity
3. TurboSMS settings/test UI parity

### Sprint B
1. VitalPBX MVP
2. Payment reconciliation improvements
3. Rozetka template + MVP

### Sprint C
1. Shop-Express full migration
2. hardening + observability
3. cutover checklist for production
