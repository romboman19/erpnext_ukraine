from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class GeneratedDocumentRef:
    doctype: str
    name: str
    purpose: str


@dataclass(frozen=True, slots=True)
class POSOrderRef:
    order_id: str
    company: str
    location: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class POSAdapter(Protocol):
    def get_order(self, order_id: str) -> POSOrderRef: ...

    def attach_allocation_preview(self, order_id: str, payload: dict[str, Any]) -> None: ...

    def split_by_fiscal_policy(self, order_id: str, groups: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def register_generated_documents(self, order_id: str, documents: list[GeneratedDocumentRef]) -> None: ...

    def queue_print(self, document: GeneratedDocumentRef, print_kind: str) -> None: ...
