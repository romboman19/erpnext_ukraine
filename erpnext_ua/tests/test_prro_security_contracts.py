from __future__ import annotations

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
FISCAL = APP / "ua_fiscal"
POS = APP / "ua_pos"


def _whitelisted_functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            value = ast.unparse(decorator)
            if "frappe.whitelist" in value:
                yield node, value


class TestPRROSecurityContracts(unittest.TestCase):
    def test_internal_fiscal_orchestrator_is_not_remotely_callable(self):
        orchestration = FISCAL / "orchestration.py"
        exposed = [node.name for node, _decorator in _whitelisted_functions(orchestration)]
        self.assertEqual(exposed, [])

        sales_invoice = FISCAL / "sales_invoice.py"
        exposed = [node.name for node, _decorator in _whitelisted_functions(sales_invoice)]
        self.assertNotIn("fiscalize_invoice", exposed)

    def test_fiscal_api_is_post_only_and_does_not_accept_a_kep_key(self):
        api = FISCAL / "api.py"
        endpoints = {node.name: (node, decorator) for node, decorator in _whitelisted_functions(api)}
        self.assertEqual(
            set(endpoints),
            {
                "fiscalize_sales_invoice",
                "register_device",
                "sync_register_state",
                "reconcile_receipt",
            },
        )
        for node, decorator in endpoints.values():
            self.assertIn("methods=['POST']", decorator)
            self.assertNotIn("kep_key", {argument.arg for argument in node.args.args})

    def test_every_fiscal_and_pos_endpoint_declares_http_methods(self):
        missing = []
        get_endpoints = []
        for root in (FISCAL, POS):
            for path in root.rglob("*.py"):
                for node, decorator in _whitelisted_functions(path):
                    if "methods=" not in decorator:
                        missing.append(f"{path.relative_to(APP)}:{node.lineno}:{node.name}")
                    if "'GET'" in decorator:
                        get_endpoints.append(node.name)
        self.assertEqual(missing, [])
        self.assertEqual(get_endpoints, [])

    def test_sales_invoice_button_uses_authorized_facade(self):
        source = (FISCAL / "doctype_js" / "sales_invoice_fiscal.js").read_text(encoding="utf-8")
        self.assertIn("erpnext_ua.ua_fiscal.api.fiscalize_sales_invoice", source)
        self.assertNotIn("erpnext_ua.ua_fiscal.sales_invoice.fiscalize_invoice", source)
        self.assertIn("!frm.doc.is_pos && !frm.doc.ua_ecommerce_channel", source)


if __name__ == "__main__":
    unittest.main()
