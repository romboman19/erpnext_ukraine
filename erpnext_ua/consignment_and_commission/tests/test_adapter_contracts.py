from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_ua.consignment_and_commission.adapters.communication import (
    CommunicationAdapter,
    MessagePayload,
    SendResult,
)
from erpnext_ua.consignment_and_commission.adapters.exchange_rate import (
    ExchangeRateAdapter,
    ExchangeRateQuote,
)
from erpnext_ua.consignment_and_commission.adapters.fiscal import (
    FiscalAdapter,
    FiscalDecision,
)
from erpnext_ua.consignment_and_commission.adapters.legal_entity import (
    EntityRef,
    LegalEntityAdapter,
)
from erpnext_ua.consignment_and_commission.adapters.pos import (
    GeneratedDocumentRef,
    POSAdapter,
    POSOrderRef,
)
from erpnext_ua.consignment_and_commission.adapters.printing import (
    PrintingAdapter,
    PrintJob,
)
from erpnext_ua.consignment_and_commission.optional_apps import (
    installed_optional_apps,
    is_app_installed,
)


class LegalEntityStub:
    def list_entities(self, company: str, user: str) -> list[EntityRef]:
        return [EntityRef("Company", company, company, company)]

    def validate_entity(self, company: str, entity: EntityRef) -> None:
        return None

    def accounting_dimension_values(self, entity: EntityRef) -> dict[str, str]:
        return entity.dimensions

    def default_accounts(self, entity: EntityRef, purpose: str) -> dict[str, str]:
        return {"purpose": purpose}

    def payment_accounts(self, entity: EntityRef) -> list[str]:
        return []


class POSStub:
    def get_order(self, order_id: str) -> POSOrderRef:
        return POSOrderRef(order_id, "Test Company", "Main", "Draft")

    def attach_allocation_preview(self, order_id: str, payload: dict) -> None:
        return None

    def split_by_fiscal_policy(self, order_id: str, groups: list[dict]) -> list[dict]:
        return groups

    def register_generated_documents(self, order_id: str, documents: list[GeneratedDocumentRef]) -> None:
        return None

    def queue_print(self, document: GeneratedDocumentRef, print_kind: str) -> None:
        return None


class CommunicationStub:
    def send(
        self, channel: str, recipient: str, payload: MessagePayload, idempotency_key: str
    ) -> SendResult:
        return SendResult(provider_id=idempotency_key, status="accepted", accepted=True)

    def status(self, provider_id: str) -> str:
        return "accepted"


class ExchangeRateStub:
    def get_rate(self, from_currency: str, to_currency: str, rate_date: date) -> ExchangeRateQuote:
        return ExchangeRateQuote(from_currency, to_currency, Decimal("1"), rate_date, "test")


class FiscalStub:
    def decide(self, company: str, legal_entity: str, relationship_model: str) -> FiscalDecision:
        return FiscalDecision("test", legal_entity, False, relationship_model)

    def register_document(self, sales_invoice: str, decision: FiscalDecision, idempotency_key: str) -> str:
        return idempotency_key


class PrintingStub:
    def enqueue(self, job: PrintJob) -> str:
        return job.idempotency_key

    def status(self, job_id: str) -> str:
        return "queued"


class AdapterContractTests(TestCase):
    def test_adapter_protocols_are_runtime_checkable(self) -> None:
        self.assertIsInstance(LegalEntityStub(), LegalEntityAdapter)
        self.assertIsInstance(POSStub(), POSAdapter)
        self.assertIsInstance(CommunicationStub(), CommunicationAdapter)
        self.assertIsInstance(ExchangeRateStub(), ExchangeRateAdapter)
        self.assertIsInstance(FiscalStub(), FiscalAdapter)
        self.assertIsInstance(PrintingStub(), PrintingAdapter)

    def test_optional_app_detection_has_no_mandatory_imports(self) -> None:
        installed = ["frappe", "erpnext", "erpnext_ua", "unrelated_app"]

        self.assertEqual(installed_optional_apps(installed), frozenset())
        self.assertTrue(is_app_installed("erpnext_ua", installed))
        self.assertFalse(is_app_installed("ukrainian_integrations", installed))
