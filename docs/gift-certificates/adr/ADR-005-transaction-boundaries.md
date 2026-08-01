# ADR-005: Transaction boundaries

Accepted. Commit durable reservation before terminal calls. External card/PRRO calls are followed by durable evidence and idempotent local steps. Unknown status is reconciled, never blindly repeated.
