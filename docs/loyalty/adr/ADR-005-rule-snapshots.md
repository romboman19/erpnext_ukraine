# ADR-005: Immutable rule snapshots і allocations

Статус: прийнято.

Опублікована конфігурація canonical-JSON hash-ується. POS/Sales Invoice зберігають hash і row snapshot. Return не запускає поточні eligibility/rate rules: він пропорційно, з final residual, сторнує/відновлює первинні item allocations. Це робить повторні та часткові повернення відтворюваними.
