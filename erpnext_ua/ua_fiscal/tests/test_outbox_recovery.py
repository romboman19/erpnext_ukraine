from __future__ import annotations

import types
import unittest
from unittest.mock import Mock, patch

from erpnext_ua.ua_fiscal import orchestration
from erpnext_ua.ua_fiscal.fiscal_client import FiscalProtocolError


def _documents():
    receipt = types.SimpleNamespace(
        doctype="PRRO Receipt",
        name="PRRO-OUTBOX-TEST",
        status="Draft",
        receipt_kind="Sale",
        cash_register="REGISTER-OUTBOX-TEST",
        shift="SHIFT-OUTBOX-TEST",
        local_number=7,
        total_amount=100,
        receipt_xml=(
            '<?xml version="1.0" encoding="windows-1251"?>'
            "<CHECK><CHECKHEAD><ORDERDATE>03082026</ORDERDATE>"
            "<ORDERTIME>193000</ORDERTIME></CHECKHEAD></CHECK>"
        ),
        creation="2026-08-03 19:30:00",
        reload=lambda: None,
    )
    register = types.SimpleNamespace(
        name="REGISTER-OUTBOX-TEST",
        fiscal_number="400000000001",
    )
    shift = types.SimpleNamespace(name="SHIFT-OUTBOX-TEST", kep_key="KEP-OUTBOX-TEST")
    return receipt, register, shift


class TestOutboxReceiptRecovery(unittest.TestCase):
    @patch.object(orchestration, "_send_online")
    @patch.object(orchestration, "_block_register", side_effect=FiscalProtocolError("blocked"))
    @patch.object(orchestration.frappe.db, "exists", return_value=False)
    @patch.object(orchestration.frappe, "get_doc")
    def test_mismatched_server_number_is_never_resent(self, get_doc, _exists, block, send):
        receipt, register, shift = _documents()
        get_doc.side_effect = [receipt, register, shift]
        client = Mock()
        client.registrar_state.return_value = {"NextLocalNum": 8}

        with self.assertRaises(FiscalProtocolError):
            orchestration._resume_pending_sale_receipt_locked(receipt.name, client)

        send.assert_not_called()
        block.assert_called_once()

    @patch.object(orchestration.frappe.db, "commit")
    @patch.object(orchestration.frappe.db, "set_value")
    @patch.object(orchestration, "_send_online")
    @patch.object(orchestration.frappe.db, "exists", return_value=False)
    @patch.object(orchestration.frappe, "get_doc")
    def test_matching_server_number_resumes_exact_ledger(
        self,
        get_doc,
        _exists,
        send,
        _set_value,
        _commit,
    ):
        receipt, register, shift = _documents()
        get_doc.side_effect = [receipt, register, shift]
        client = Mock()
        client.registrar_state.return_value = {"NextLocalNum": 7}

        def confirm(_client, current, _xml, _key):
            current.status = "Fiscalized"
            return {"order_tax_num": "700000000001"}

        send.side_effect = confirm

        result = orchestration._resume_pending_sale_receipt_locked(receipt.name, client)

        self.assertEqual(result.status, "Fiscalized")
        send.assert_called_once()
        self.assertEqual(send.call_args.args[3], "KEP-OUTBOX-TEST")


if __name__ == "__main__":
    unittest.main()
