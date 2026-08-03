# Architecture

`POS Order` залишається saga root. UI/API лише збирають контекст. `domain/` виконує Decimal/policy calculations; `services/` володіють locks, ledger та lifecycle; adapters перекладають результат у POS/Sales Invoice/GL/PRRO/loyalty.

Sale: durable issue → confirmed money → Gift Certificate Sale + JE → activation → fiscalization → protected print.

Redemption: quote → row/version check → durable reservation/commit → top-up → Sales Invoice → consume ledger → item allocations → settlement → PRRO.

Return: barcode первинного чека → original POS/SI item → original certificate allocation → exact paid/promotional restore → inverse GL/settlement.
