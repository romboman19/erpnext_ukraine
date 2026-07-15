from __future__ import annotations

import ast
import csv
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "ukrainian_integrations"


class ProductionStaticContractsTest(unittest.TestCase):
    def test_all_python_and_json_sources_parse(self):
        for path in PACKAGE.rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in PACKAGE.rglob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))

    def test_whitelisted_methods_are_explicitly_authorized(self):
        guest_allowlist = {"liqpay_callback", "webhook", "webhook_event"}
        missing = []
        unexpected_guest = []
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                decorators = [ast.unparse(item) for item in node.decorator_list]
                whitelist = next((item for item in decorators if "frappe.whitelist" in item), None)
                if not whitelist:
                    continue
                allow_guest = "allow_guest=True" in whitelist
                calls = [
                    child.func.id
                    for child in ast.walk(node)
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                ]
                if allow_guest:
                    if node.name not in guest_allowlist:
                        unexpected_guest.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")
                elif "require_roles" not in calls:
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")
        self.assertEqual(unexpected_guest, [], f"Unexpected guest endpoints: {unexpected_guest}")
        self.assertEqual(missing, [], f"Whitelisted endpoints without require_roles: {missing}")

    def test_no_blind_financial_retry(self):
        payment_tree = PACKAGE / "payments"
        offenders = []
        for path in payment_tree.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "time.sleep(" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_paid_invoices_cannot_start_a_new_liqpay_payment(self):
        liqpay = (PACKAGE / "payments" / "liqpay" / "service.py").read_text(encoding="utf-8")
        self.assertIn("Sales Invoice has no positive outstanding amount", liqpay)
        self.assertNotIn("si.outstanding_amount or si.grand_total", liqpay)

    def test_liqpay_defaults_to_v7_and_keeps_explicit_v3_callback_compatibility(self):
        client = (PACKAGE / "payments" / "liqpay" / "client.py").read_text(encoding="utf-8")
        service = (PACKAGE / "payments" / "liqpay" / "service.py").read_text(encoding="utf-8")
        self.assertIn("hashlib.sha3_256", client)
        self.assertIn("SUPPORTED_API_VERSIONS = {3, 7}", client)
        self.assertIn('liqpay_api_version", 7', service)
        self.assertNotIn('log_event("liqpay", "error", "Invalid callback signature"', service)
        self.assertIn("must match the selected server-side profile", service)
        self.assertIn('decoded.get("payment_id")', service)
        self.assertIn('if status == "reversed":', service)
        self.assertIn('"reconciliation_required": True', service)

    def test_required_production_doctypes_are_shipped(self):
        required = {
            "Customer Birthday Greeting Log",
            "Customer Identification Request",
            "Customer Identification Settings",
            "NP Sender Profile",
            "NP Sender Branch Row",
            "UP Sender Profile",
            "TurboSMS Settings",
            "TurboSMS Sender",
            "TurboSMS Log",
            "RZ Delivery Sender Profile",
            "UA Integration Operation",
        }
        found = set()
        for path in (PACKAGE / "ukrainian_integrations" / "doctype").glob("*/*.json"):
            found.add(json.loads(path.read_text(encoding="utf-8"))["name"])
        self.assertTrue(required.issubset(found), required - found)

    def test_turbosms_log_keeps_upgrade_safe_hash_names(self):
        path = (
            PACKAGE
            / "ukrainian_integrations"
            / "doctype"
            / "turbosms_log"
            / "turbosms_log.json"
        )
        definition = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(definition.get("autoname"), "hash")

    def test_external_mutations_require_idempotency_keys(self):
        required = {
            "liqpay_initiate",
            "send_sms",
            "send_sms_from_settings",
            "send_sms_to_customer",
            "click_to_call",
            "click_to_call_customer",
            "dialer_call",
            "create_ttn_standalone",
            "create_ttn_from_sales_invoice",
            "create_shipment_standalone",
            "create_shipment_from_sales_invoice",
            "up_create_address",
            "up_create_client",
            "create_track_standalone",
            "create_track_from_sales_invoice",
        }
        found = {}
        for path in PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                decorators = [ast.unparse(item) for item in getattr(node, "decorator_list", [])]
                if (
                    isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                    and node.name in required
                    and any("frappe.whitelist" in item for item in decorators)
                ):
                    found[node.name] = {arg.arg for arg in node.args.args}
        self.assertEqual(set(found), required)
        self.assertEqual(
            {name for name, arguments in found.items() if "idempotency_key" not in arguments},
            set(),
        )

    def test_bank_import_uses_exact_unique_key(self):
        mono = (PACKAGE / "payments" / "monobank" / "service.py").read_text(encoding="utf-8")
        privat = (PACKAGE / "payments" / "privatbank" / "service.py").read_text(encoding="utf-8")
        self.assertIn('"ua_integration_key": integration_key', mono)
        self.assertIn("'ua_integration_key': integration_key", privat)
        self.assertNotIn('"description": ["like"', mono)
        self.assertNotIn("'description': ['like'", privat)

    def test_ci_runs_tests_not_only_compile(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        integration_script = (ROOT / "scripts" / "ci_frappe_integration.sh").read_text(encoding="utf-8")
        self.assertIn("unittest", workflow)
        self.assertIn("frappe-integration", workflow)
        self.assertIn("run_installation_checks", integration_script)
        self.assertIn("set-config allow_tests true", integration_script)
        self.assertIn("run-tests --app ukrainian_integrations", integration_script)
        self.assertNotIn("pip install --no-deps", integration_script)
        self.assertIn('find ukrainian_integrations/public -name "*.js"', workflow)
        self.assertIn("pip-audit --strict --progress-spinner off .", workflow)

    def test_frappe_app_has_required_discovery_files(self):
        for filename in ("hooks.py", "modules.txt", "patches.txt"):
            self.assertTrue((PACKAGE / filename).is_file(), filename)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"patches.txt"', pyproject)

    def test_app_screen_workspace_and_ukrainian_translation_are_shipped(self):
        hooks = (PACKAGE / "hooks.py").read_text(encoding="utf-8")
        self.assertIn("add_to_apps_screen", hooks)
        self.assertIn('app_home = "/app/ukrainian-integrations"', hooks)

        desktop_icon = json.loads(
            (PACKAGE / "desktop_icon" / "ukrainian_integrations.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(desktop_icon["icon_type"], "App")
        self.assertEqual(desktop_icon["app"], "ukrainian_integrations")
        self.assertFalse(desktop_icon["hidden"])

        workspace = json.loads(
            (
                PACKAGE
                / "ukrainian_integrations"
                / "workspace"
                / "ukrainian_integrations"
                / "ukrainian_integrations.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(workspace["app"], "ukrainian_integrations")
        self.assertEqual(workspace["module"], "Ukrainian Integrations")
        self.assertTrue(workspace["public"])
        self.assertFalse(workspace["is_hidden"])

        translations = PACKAGE / "translations" / "uk.csv"
        with translations.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        translation_map = {row[0]: row[1] for row in rows}
        self.assertGreaterEqual(len(translation_map), 350)
        self.assertEqual(translation_map["Ukrainian Integrations"], "Українські інтеграції")
        self.assertEqual(translation_map["Delivery"], "Доставка")
        for source, translated, *_ in rows:
            self.assertTrue(source)
            self.assertTrue(translated)
            self.assertEqual(
                set(re.findall(r"\{[^}]+\}", source)),
                set(re.findall(r"\{[^}]+\}", translated)),
            )

    def test_prom_stock_contract_matches_official_external_id_endpoint(self):
        client = (PACKAGE / "ecommerce" / "providers" / "prom_ua" / "api.py").read_text(
            encoding="utf-8"
        )
        service = (PACKAGE / "ecommerce" / "providers" / "prom_ua" / "service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("/products/edit_by_external_id", client)
        self.assertIn('"quantity_in_stock"', service)
        self.assertIn('"last_id"', client)
        self.assertNotIn('"page": int(page)', client)

    def test_secret_debugging_never_returns_key_prefixes(self):
        source = (PACKAGE / "shipment" / "nova_poshta" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("[:8]", source)
        self.assertIn("api_key_configured", source)

    def test_provider_identifiers_cannot_escape_url_paths(self):
        up_api = (PACKAGE / "shipment" / "ukr_poshta" / "api.py").read_text(encoding="utf-8")
        up_service = (PACKAGE / "shipment" / "ukr_poshta" / "service.py").read_text(encoding="utf-8")
        np_service = (PACKAGE / "shipment" / "nova_poshta" / "service.py").read_text(encoding="utf-8")
        rz_api = (PACKAGE / "shipment" / "rozetka_delivery" / "api.py").read_text(encoding="utf-8")
        rz_service = (PACKAGE / "shipment" / "rozetka_delivery" / "service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("quote(str(barcode), safe='')", up_api)
        self.assertIn("def _validated_barcode", up_service)
        self.assertIn('re.fullmatch(r"[A-Za-z0-9-]{1,64}", ttn_ref)', np_service)
        self.assertIn('quote(str(track_id), safe="")', rz_api)
        self.assertIn("def _validated_track_id", rz_service)

    def test_rozetka_delivery_contract_is_wired_for_production(self):
        hooks = (PACKAGE / "hooks.py").read_text(encoding="utf-8")
        service = (PACKAGE / "shipment" / "rozetka_delivery" / "service.py").read_text(
            encoding="utf-8"
        )
        api = (PACKAGE / "shipment" / "rozetka_delivery" / "api.py").read_text(encoding="utf-8")
        operations = (PACKAGE / "utils" / "operations.py").read_text(encoding="utf-8")
        logger = (PACKAGE / "utils" / "logger.py").read_text(encoding="utf-8")
        self.assertIn("rozetka_delivery.scheduler.sync_track_statuses", hooks)
        self.assertIn('payload={"data": data}', api)
        self.assertIn('"api/track/status", params={"id": track_ids}', api)
        self.assertIn('"api/track/label"', api)
        self.assertIn('integration="rozetka_delivery"', service)
        self.assertIn('@frappe.whitelist(methods=["POST"])', service)
        self.assertIn('"rozetka_delivery"', operations)
        self.assertIn('"api_token"', logger)

    def test_customer_identification_is_authorized_and_does_not_disclose_pii_early(self):
        service = (PACKAGE / "customer_identification" / "service.py").read_text(
            encoding="utf-8"
        )
        telegram = (PACKAGE / "customer_identification" / "telegram.py").read_text(
            encoding="utf-8"
        )
        birthday = (PACKAGE / "customer_identification" / "birthday.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("IDENTIFICATION_ROLES", service)
        self.assertIn('if doc.status == "Verified" else None', service)
        self.assertIn('idempotency_key=sms_key', service)
        self.assertIn("SELECT name FROM `tabCustomer Identification Request`", service)
        self.assertIn("telegram_webhook_secret", telegram)
        self.assertIn("secrets_equal", telegram)
        self.assertIn("allow_redirects=False", telegram)
        self.assertIn('log.status = "Unknown"', birthday)

    def test_sensitive_logs_have_partition_or_manager_only_access(self):
        hooks = (PACKAGE / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"VitalPBX Call Log"', hooks)
        self.assertIn('"UA Integration Operation"', hooks)

    def test_idempotency_insert_only_recovers_the_unique_key_race(self):
        source = (PACKAGE / "utils" / "operations.py").read_text(encoding="utf-8")
        reserve_source = source[source.index("def reserve_operation") : source.index("def mark_operation")]
        self.assertIn("except frappe.DuplicateEntryError:", source)
        self.assertNotIn("except Exception:", reserve_source)

    def test_all_persisted_log_text_is_sanitized_and_bounded(self):
        source = (PACKAGE / "utils" / "logger.py").read_text(encoding="utf-8")
        self.assertIn('"message": sanitize_text(message)[:1000]', source)
        self.assertIn('"error_trace": _dump(error_trace)', source)


if __name__ == "__main__":
    unittest.main()
