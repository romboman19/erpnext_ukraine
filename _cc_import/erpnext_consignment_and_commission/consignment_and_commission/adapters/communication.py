from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class MessagePayload:
    subject: str
    body: str
    reference_doctype: str
    reference_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendResult:
    provider_id: str
    status: str
    accepted: bool
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CommunicationAdapter(Protocol):
    def send(self, channel: str, recipient: str, payload: MessagePayload, idempotency_key: str) -> SendResult: ...

    def status(self, provider_id: str) -> str: ...
