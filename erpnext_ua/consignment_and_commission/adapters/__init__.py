"""Ports for optional ERPNext, POS and provider integrations."""

from .communication import CommunicationAdapter, MessagePayload, SendResult
from .exchange_rate import ExchangeRateAdapter, ExchangeRateQuote
from .fiscal import FiscalAdapter, FiscalDecision
from .legal_entity import EntityRef, LegalEntityAdapter
from .pos import GeneratedDocumentRef, POSAdapter
from .printing import PrintingAdapter, PrintJob

__all__ = [
    "CommunicationAdapter",
    "EntityRef",
    "ExchangeRateAdapter",
    "ExchangeRateQuote",
    "FiscalAdapter",
    "FiscalDecision",
    "GeneratedDocumentRef",
    "LegalEntityAdapter",
    "MessagePayload",
    "POSAdapter",
    "PrintJob",
    "PrintingAdapter",
    "SendResult",
]
