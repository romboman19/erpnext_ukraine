from types import SimpleNamespace
from unittest import TestCase

from erpnext_ua.consignment_and_commission.spikes.foundation import (
    CONFIRMATION as FOUNDATION_CONFIRMATION,
)
from erpnext_ua.consignment_and_commission.spikes.foundation import (
    _assert_test_scope as assert_foundation_test_scope,
)
from erpnext_ua.consignment_and_commission.spikes.inventory_dimension import (
    CONFIRMATION,
    _assert_test_scope,
)


class SpikeSafetyTests(TestCase):
    def test_inventory_dimension_spike_accepts_explicit_test_scope(self) -> None:
        frappe = SimpleNamespace(local=SimpleNamespace(site="postest.local"))

        _assert_test_scope(
            frappe,
            confirm_site="postest.local",
            confirm_write=CONFIRMATION,
            company="POS Test Ukraine",
        )

    def test_inventory_dimension_spike_rejects_non_allowlisted_site(self) -> None:
        frappe = SimpleNamespace(local=SimpleNamespace(site="production.local"))

        with self.assertRaises(RuntimeError):
            _assert_test_scope(
                frappe,
                confirm_site="production.local",
                confirm_write=CONFIRMATION,
                company="POS Test Ukraine",
            )

    def test_inventory_dimension_spike_requires_confirmation(self) -> None:
        frappe = SimpleNamespace(local=SimpleNamespace(site="postest.local"))

        with self.assertRaises(RuntimeError):
            _assert_test_scope(
                frappe,
                confirm_site="postest.local",
                confirm_write="",
                company="POS Test Ukraine",
            )

    def test_foundation_smoke_is_test_site_allow_listed(self) -> None:
        frappe = SimpleNamespace(local=SimpleNamespace(site="postest.local"))
        assert_foundation_test_scope(
            frappe,
            confirm_site="postest.local",
            confirm_write=FOUNDATION_CONFIRMATION,
            company="POS Test Ukraine",
        )

    def test_foundation_smoke_rejects_production(self) -> None:
        frappe = SimpleNamespace(local=SimpleNamespace(site="production.local"))
        with self.assertRaises(RuntimeError):
            assert_foundation_test_scope(
                frappe,
                confirm_site="production.local",
                confirm_write=FOUNDATION_CONFIRMATION,
                company="POS Test Ukraine",
            )
