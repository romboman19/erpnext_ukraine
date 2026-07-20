from datetime import date
from decimal import Decimal
from unittest import TestCase

from erpnext_consignment_and_commission.consignment_and_commission.services.foundation import (
    ContractPolicy,
    FoundationValidationError,
    LocationPolicy,
    SettingsPolicy,
    date_ranges_overlap,
    partner_allows_relationship,
    validate_contract_policy,
    validate_location_policy,
    validate_settings_policy,
)


class FoundationPolicyTests(TestCase):
    def test_settings_require_an_enabled_relationship(self) -> None:
        with self.assertRaisesRegex(FoundationValidationError, "At least one"):
            validate_settings_policy(SettingsPolicy(False, False, 15, 3))

    def test_settings_limits_are_bounded(self) -> None:
        with self.assertRaisesRegex(FoundationValidationError, "TTL"):
            validate_settings_policy(SettingsPolicy(True, True, 0, 3))
        with self.assertRaisesRegex(FoundationValidationError, "retry"):
            validate_settings_policy(SettingsPolicy(True, True, 15, 11))

    def test_location_requires_three_distinct_warehouses(self) -> None:
        with self.assertRaisesRegex(FoundationValidationError, "distinct"):
            validate_location_policy(
                LocationPolicy("Company", "Company", "Company", "Own", "Third Party", "Third Party")
            )

    def test_partner_both_allows_either_contract_model(self) -> None:
        self.assertTrue(partner_allows_relationship("BOTH", "COMMISSION"))
        self.assertTrue(partner_allows_relationship("BOTH", "CONSIGNMENT"))
        self.assertFalse(partner_allows_relationship("COMMISSION", "CONSIGNMENT"))

    def test_commission_contract_requires_positive_rate(self) -> None:
        with self.assertRaisesRegex(FoundationValidationError, "require a rate"):
            validate_contract_policy(self._contract("COMMISSION", Decimal("0")))

    def test_consignment_contract_rejects_commission_rate(self) -> None:
        with self.assertRaisesRegex(FoundationValidationError, "cannot define"):
            validate_contract_policy(self._contract("CONSIGNMENT", Decimal("5")))

    def test_contract_end_date_cannot_precede_start(self) -> None:
        policy = ContractPolicy(
            relationship_model="COMMISSION",
            status="ACTIVE",
            valid_from=date(2026, 7, 2),
            valid_to=date(2026, 7, 1),
            commission_rate=Decimal("10"),
            settlement_deadline_days=7,
            fiscal_policy="AUTO",
            price_authority="COMPANY",
        )
        with self.assertRaisesRegex(FoundationValidationError, "end date"):
            validate_contract_policy(policy)

    def test_open_ended_date_ranges_overlap(self) -> None:
        self.assertTrue(date_ranges_overlap(date(2026, 1, 1), None, date(2030, 1, 1), date(2030, 2, 1)))
        self.assertFalse(
            date_ranges_overlap(date(2026, 1, 1), date(2026, 6, 30), date(2026, 7, 1), None)
        )

    @staticmethod
    def _contract(model: str, rate: Decimal) -> ContractPolicy:
        return ContractPolicy(
            relationship_model=model,
            status="ACTIVE",
            valid_from=date(2026, 7, 1),
            valid_to=None,
            commission_rate=rate,
            settlement_deadline_days=7,
            fiscal_policy="AUTO",
            price_authority="COMPANY",
        )
