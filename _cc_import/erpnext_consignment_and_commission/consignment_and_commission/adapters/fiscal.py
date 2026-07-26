from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FiscalDecision:
    policy: str
    route: str
    requires_fiscal_receipt: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class FiscalAdapter(Protocol):
    def decide(self, company: str, legal_entity: str, relationship_model: str) -> FiscalDecision: ...

    def register_document(self, sales_invoice: str, decision: FiscalDecision, idempotency_key: str) -> str: ...
