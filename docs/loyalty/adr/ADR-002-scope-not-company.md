# ADR-002: Account належить Scope, не Company

Статус: прийнято.

Унікальний account визначається `(Customer, Scope)`. Location mapping пов’язує каси, склади, Company/FOP із Scope. Це дає один баланс у мережі, але зберігає issuer/redeemer Company в ledger для аудиту. Окремі Scope мають незалежні баланси.
