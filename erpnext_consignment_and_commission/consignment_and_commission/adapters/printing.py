from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PrintJob:
    reference_doctype: str
    reference_name: str
    print_kind: str
    idempotency_key: str
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PrintingAdapter(Protocol):
    def enqueue(self, job: PrintJob) -> str: ...

    def status(self, job_id: str) -> str: ...
