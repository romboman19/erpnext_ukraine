from __future__ import annotations

import ast
import csv
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "erpnext_ua"
# The connector code that used to be the `ukrainian_integrations` app now lives
# in three places: the two Frappe modules, which have to sit at the app root,
# and the supporting packages under `integrations/`.
INTEGRATIONS = APP / "integrations"
ECOMMERCE = APP / "ecommerce"
UI_MODULE = APP / "ukrainian_integrations"
MODULES = (UI_MODULE, ECOMMERCE)
SOURCE_ROOTS = (INTEGRATIONS, ECOMMERCE, UI_MODULE, APP / "patches")


def integration_sources(pattern: str):
	for root in SOURCE_ROOTS:
		yield from root.rglob(pattern)


class ProductionStaticContractsTest(unittest.TestCase):
    def test_all_python_and_json_sources_parse(self):
        for path in integration_sources("*.py"):
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for path in integration_sources("*.json"):
            json.loads(path.read_text(encoding="utf-8"))

    def test_whitelisted_methods_are_explicitly_authorized(self):
        guest_allowlist = {"liqpay_callback", "webhook", "webhook_event"}
        missing = []
        unexpected_guest = []
        for path in integration_sources("*.py"):
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
        payment_tree = INTEGRATIONS / "payments"
        offenders = []
        for path in payment_tree.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "time.sleep(" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_paid_invoices_cannot_start_a_new_liqpay_payment(self):
        liqpay = (INTEGRATIONS / "payments" / "liqpay" / "service.py").read_text(encoding="utf-8")
        self.assertIn("Sales Invoice has no positive outstanding amount", liqpay)
        self.assertNotIn("si.outstanding_amount or si.grand_total", liqpay)

    def test_liqpay_defaults_to_v7_and_keeps_explicit_v3_callback_compatibility(self):
        client = (INTEGRATIONS / "payments" / "liqpay" / "client.py").read_text(encoding="utf-8")
        service = (INTEGRATIONS / "payments" / "liqpay" / "service.py").read_text(encoding="utf-8")
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
            "Customer Telegram Link",
            "Ecommerce Channel",
            "Ecommerce Customer Mapping",
            "Ecommerce File Field",
            "Ecommerce File Exchange",
            "Ecommerce File Layout",
            "Ecommerce Item Mapping",
            "Ecommerce Order Status Map",
            "Ecommerce Payment Route",
            "Ecommerce Status Mapping",
            "Ecommerce Sync Entity Config",
            "Ecommerce Sync Log",
            "Ecommerce Warehouse Sync",
            "Ecommerce Warehouse Mapping",
            "File Delivery Endpoint",
            "NP Sender Profile",
            "NP Sender Branch Row",
            "OcStore Settings",
            "UP Sender Profile",
            "TurboSMS Settings",
            "TurboSMS Sender",
            "TurboSMS Log",
            "Telegram Bot Profile",
            "RZ Delivery Sender Profile",
            "UA Integration Operation",
        }
        found = set()
        for path in [json_path for module in MODULES for json_path in module.glob("doctype/*/*.json")]:
            found.add(json.loads(path.read_text(encoding="utf-8"))["name"])
        self.assertTrue(required.issubset(found), required - found)

    def test_installation_diagnostics_match_the_consolidated_app(self):
        diagnostics = (INTEGRATIONS / "diagnostics.py").read_text(encoding="utf-8")
        self.assertIn('for app in ("erpnext", "erpnext_ua")', diagnostics)
        self.assertNotIn('for app in ("erpnext", "ukrainian_integrations")', diagnostics)
        self.assertNotIn('"telegram_bot_token",', diagnostics)

    def test_turbosms_log_keeps_upgrade_safe_hash_names(self):
        path = (
            UI_MODULE
            / "doctype"
            / "turbosms_log"
            / "turbosms_log.json"
        )
        definition = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(definition.get("autoname"), "hash")

    def test_modules_select_turbosms_senders_from_the_local_registry(self):
        settings_path = (
            UI_MODULE
            / "doctype"
            / "customer_identification_settings"
            / "customer_identification_settings.json"
        )
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        sms_sender = next(field for field in settings["fields"] if field["fieldname"] == "sms_sender")
        self.assertEqual(sms_sender["fieldtype"], "Select")

        controller = settings_path.with_suffix(".js").read_text(encoding="utf-8")
        self.assertIn("get_sender_options", controller)
        self.assertNotIn('fieldtype: "Data"', controller)

        service = (INTEGRATIONS / "pbx_sms" / "sms" / "turbosms.py").read_text(encoding="utf-8")
        self.assertIn("resolve_configured_sender", service)
        self.assertIn("Sender is not configured or inactive", service)

    def test_legacy_shipping_settings_and_turbosms_sender_field_are_removed(self):
        settings_path = (
            UI_MODULE
            / "doctype"
            / "turbosms_settings"
            / "turbosms_settings.json"
        )
        definition = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertNotIn("sender", {field["fieldname"] for field in definition["fields"]})

        service = (INTEGRATIONS / "pbx_sms" / "sms" / "turbosms.py").read_text(encoding="utf-8")
        self.assertNotIn('s.get("sender")', service)

        migrations = (INTEGRATIONS / "migrations.py").read_text(encoding="utf-8")
        self.assertIn('("NP Integration Settings", "UP Integration Settings")', migrations)
        self.assertIn('frappe.delete_doc("DocType", doctype', migrations)

        translations = (APP / "translations" / "uk.csv").read_text(encoding="utf-8")
        self.assertNotIn('"Legacy Default Sender",', translations)
        self.assertNotIn('"NP Integration Settings",', translations)
        self.assertNotIn('"UP Integration Settings",', translations)

    def test_external_mutations_require_idempotency_keys(self):
        required = {
            "liqpay_initiate",
            "send_sms",
            "send_sms_from_settings",
            "send_sms_to_customer",
            "enqueue_telegram_message",
            "send_test_message",
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
        for path in integration_sources("*.py"):
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
        mono = (INTEGRATIONS / "payments" / "monobank" / "service.py").read_text(encoding="utf-8")
        privat = (INTEGRATIONS / "payments" / "privatbank" / "service.py").read_text(encoding="utf-8")
        self.assertIn('"ua_integration_key": integration_key', mono)
        self.assertIn("'ua_integration_key': integration_key", privat)
        self.assertNotIn('"description": ["like"', mono)
        self.assertNotIn("'description': ['like'", privat)

    def test_ci_runs_tests_not_only_compile(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        integration_script = (ROOT / "tools" / "ci_frappe_integration.sh").read_text(encoding="utf-8")
        self.assertIn("unittest", workflow)
        self.assertIn("frappe-integration", workflow)
        self.assertIn("run_installation_checks", integration_script)
        self.assertIn("set-config allow_tests true", integration_script)
        self.assertIn("run-tests --app erpnext_ua", integration_script)
        self.assertNotIn("pip install --no-deps", integration_script)
        self.assertIn("""find erpnext_ua -name '*.js'""", workflow)
        self.assertIn("pip-audit --strict --progress-spinner off .", workflow)

    def test_frappe_app_has_required_discovery_files(self):
        for filename in ("hooks.py", "modules.txt", "patches.txt"):
            self.assertTrue((APP / filename).is_file(), filename)
        # The consolidated app ships manifests by glob rather than by name.
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"**/*.txt"', pyproject)
        self.assertIn('"**/*.js"', pyproject)

    def test_app_screen_workspace_and_ukrainian_translation_are_shipped(self):
        # The integrations workspace is no longer the app landing page; it stays
        # reachable through its own desk icon, asserted below.
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn("add_to_apps_screen", hooks)
        self.assertIn('app_home = "/app/ua-fop"', hooks)

        desktop_icon = json.loads(
            (APP / "desktop_icon" / "ukrainian_integrations.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(desktop_icon["icon_type"], "App")
        self.assertEqual(desktop_icon["app"], "erpnext_ua")
        self.assertFalse(desktop_icon["hidden"])

        workspace = json.loads(
            (
                UI_MODULE
                / "workspace"
                / "ukrainian_integrations"
                / "ukrainian_integrations.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(workspace["app"], "erpnext_ua")
        self.assertEqual(workspace["module"], "Ukrainian Integrations")
        self.assertTrue(workspace["public"])
        self.assertFalse(workspace["is_hidden"])
        workspace_links = {
            (link.get("label"), link.get("link_to")) for link in workspace["links"]
        }
        self.assertIn(("E-commerce", None), workspace_links)
        self.assertIn(("Ecommerce File Layouts", "Ecommerce File Layout"), workspace_links)
        self.assertIn(("Ecommerce Sync Logs", "Ecommerce Sync Log"), workspace_links)
        self.assertNotIn(("Ecommerce Channels", "Ecommerce Channel"), workspace_links)
        self.assertNotIn(("Ecommerce File Exchange", "Ecommerce File Exchange"), workspace_links)

        sidebar = json.loads(
            (APP / "workspace_sidebar" / "ukrainian_integrations.json").read_text(
                encoding="utf-8"
            )
        )
        sidebar_links = {
            (item.get("label"), item.get("link_to")) for item in sidebar["items"]
        }
        self.assertIn(("E-commerce", None), sidebar_links)
        self.assertIn(("File Layouts", "Ecommerce File Layout"), sidebar_links)
        self.assertIn(("Sync Logs", "Ecommerce Sync Log"), sidebar_links)
        self.assertNotIn(("Channels", "Ecommerce Channel"), sidebar_links)
        self.assertNotIn(("File Exchange", "Ecommerce File Exchange"), sidebar_links)

        translations = APP / "translations" / "uk.csv"
        with translations.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        # The central catalog keys on source *and* context, so a source may
        # legitimately appear more than once with different contexts.
        translation_map = {row[0]: row[1] for row in rows if len(row) < 3 or not row[2]}
        keys = {(row[0], row[2] if len(row) > 2 else "") for row in rows}
        self.assertEqual(len(rows), len(keys))
        self.assertGreaterEqual(len(translation_map), 520)
        self.assertEqual(translation_map["Ukrainian Integrations"], "Українські інтеграції")
        self.assertEqual(translation_map["Delivery"], "Доставка")
        self.assertEqual(translation_map["Framework"], "Платформа Frappe")
        self.assertEqual(translation_map["Apps"], "Застосунки")
        self.assertEqual(translation_map["Apply"], "Застосувати")
        self.assertEqual(translation_map["Are you sure?"], "Ви впевнені?")
        self.assertEqual(translation_map["TTN is required"], "Потрібна ТТН")
        self.assertEqual(translation_map["System Health Report"], "Звіт про стан системи")
        self.assertEqual(translation_map["Background Workers"], "Фонові обробники")
        self.assertEqual(translation_map["Scheduler Status"], "Стан планувальника")
        self.assertEqual(translation_map["Binary Logging"], "Бінарне журналювання")
        self.assertEqual(translation_map["Failure Rate"], "Частка помилок")

        migrations = (INTEGRATIONS / "migrations.py").read_text(encoding="utf-8")
        self.assertIn('"Desktop Layout"', migrations)
        self.assertIn('("desktop_icons", "bootinfo")', migrations)
        self.assertIn("_CUSTOM_DESK_APPS", migrations)
        # Row-level catalog invariants (non-empty, placeholder and e-mail
        # preservation, unique keys) belong to erpnext_ua/tests/test_translations.py,
        # which owns the merged catalog and checks every row.

    def test_system_health_report_is_container_aware(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        monitoring = (INTEGRATIONS / "monitoring" / "system_health.py").read_text(encoding="utf-8")
        self.assertIn('"System Health Report"', hooks)
        self.assertIn("ContainerAwareSystemHealthReport", hooks)
        self.assertIn('"* * * * *"', hooks)
        self.assertIn("update_scheduler_heartbeat", hooks)
        self.assertIn("get_workers()", monitoring)
        self.assertIn("serialize_worker", monitoring)
        self.assertIn('self.scheduler_status = "Active"', monitoring)

    def test_gsf_expiry_sweeper_is_scheduled(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        allocations = (
            APP / "group_stock_fifo" / "services" / "allocations.py"
        ).read_text(encoding="utf-8")

        self.assertIn('f"{GSF}.services.allocations.expire_due_allocations"', hooks)
        self.assertIn("def expire_due_allocations(limit: int = 500)", allocations)
        self.assertNotIn("not wired into `scheduler_events`", allocations)

    def test_prom_stock_contract_matches_official_external_id_endpoint(self):
        client = (ECOMMERCE / "providers" / "prom_ua" / "api.py").read_text(
            encoding="utf-8"
        )
        service = (ECOMMERCE / "providers" / "prom_ua" / "service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("/products/edit_by_external_id", client)
        self.assertIn('"quantity_in_stock"', service)
        self.assertIn('"last_id"', client)
        self.assertNotIn('"page": int(page)', client)

    def test_ecommerce_base_replaces_the_universal_channel_runtime(self):
        endpoint = json.loads(
            (
                ECOMMERCE
                / "doctype"
                / "file_delivery_endpoint"
                / "file_delivery_endpoint.json"
            ).read_text(encoding="utf-8")
        )
        endpoint_fields = {field["fieldname"]: field for field in endpoint["fields"]}
        self.assertFalse(endpoint.get("issingle", False))
        self.assertEqual(endpoint_fields["password"]["fieldtype"], "Password")
        self.assertEqual(endpoint_fields["ssh_key"]["fieldtype"], "Password")

        mapping = json.loads(
            (
                ECOMMERCE
                / "doctype"
                / "ecommerce_item_mapping"
                / "ecommerce_item_mapping.json"
            ).read_text(encoding="utf-8")
        )
        mapping_fields = {field["fieldname"]: field for field in mapping["fields"]}
        self.assertEqual(mapping_fields["channel"]["fieldtype"], "Data")
        self.assertIn("last_export_hash", mapping_fields)

        transform_source = (
            ECOMMERCE / "base" / "serializers" / "transforms.py"
        ).read_text(encoding="utf-8")
        field_controller = (
            ECOMMERCE
            / "doctype"
            / "ecommerce_file_field"
            / "ecommerce_file_field.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_CUSTOM_TRANSFORMS", transform_source)
        self.assertIn("is_registered_custom_transform", field_controller)
        self.assertNotIn("frappe.get_attr", transform_source)
        self.assertNotIn("importlib", transform_source)

        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertNotIn("ecommerce.core.scheduler", hooks)
        self.assertIn("backfill_ecommerce_item_mapping", (APP / "patches.txt").read_text())

    def test_ocstore_file_cycle_is_multi_record_and_all_or_keep(self):
        settings = json.loads(
            (
                ECOMMERCE
                / "doctype"
                / "ocstore_settings"
                / "ocstore_settings.json"
            ).read_text(encoding="utf-8")
        )
        fields = {field["fieldname"]: field for field in settings["fields"]}
        self.assertFalse(settings.get("issingle", False))
        self.assertEqual(fields["company"].get("options"), "Company")
        self.assertEqual(fields["sync_entities"].get("options"), "Ecommerce Sync Entity Config")

        service = (
            ECOMMERCE / "providers" / "ocstore" / "service.py"
        ).read_text(encoding="utf-8")
        transaction_commit = service.index("# Acceptance invariant")
        delete_call = service.index("deletion = transport.delete(remote_name)")
        self.assertLess(transaction_commit, delete_call)
        self.assertIn("frappe.db.rollback()", service)
        self.assertIn('status="Failed"', service)
        self.assertIn("deliberately keep the FTP file", service)

        ftp = (ECOMMERCE / "base" / "transport" / "ftp.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("paramiko.RejectPolicy()", ftp)
        self.assertIn("load_system_host_keys()", ftp)
        self.assertNotIn("AutoAddPolicy", ftp)

        order_intake = (ECOMMERCE / "base" / "orders.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("payment_key =", order_intake)
        self.assertIn("'channel_order_id': order.channel_order_id", order_intake)
        self.assertIn("_converge_sales_order_invoice_payment", order_intake)
        self.assertIn("_find_matching_payment_entry", order_intake)
        self.assertIn("retry_failed=True", order_intake)
        self.assertIn("continue to\n        # Payment Entry reconciliation", order_intake)

        catalog = (
            ECOMMERCE / "providers" / "ocstore" / "catalog.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Sum(bin_row.actual_qty)", catalog)
        self.assertNotIn('"sum(actual_qty) as actual_qty"', catalog)
        patches = (APP / "patches.txt").read_text(encoding="utf-8")
        self.assertIn("create_ocstore_defaults_and_migrate_channels", patches)

    def test_secret_debugging_never_returns_key_prefixes(self):
        source = (INTEGRATIONS / "shipment" / "nova_poshta" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("[:8]", source)
        self.assertIn("api_key_configured", source)

    def test_provider_identifiers_cannot_escape_url_paths(self):
        up_api = (INTEGRATIONS / "shipment" / "ukr_poshta" / "api.py").read_text(encoding="utf-8")
        up_service = (INTEGRATIONS / "shipment" / "ukr_poshta" / "service.py").read_text(encoding="utf-8")
        np_service = (INTEGRATIONS / "shipment" / "nova_poshta" / "service.py").read_text(encoding="utf-8")
        rz_api = (INTEGRATIONS / "shipment" / "rozetka_delivery" / "api.py").read_text(encoding="utf-8")
        rz_service = (INTEGRATIONS / "shipment" / "rozetka_delivery" / "service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("quote(str(barcode), safe='')", up_api)
        self.assertIn("def _validated_barcode", up_service)
        self.assertIn('re.fullmatch(r"[A-Za-z0-9-]{1,64}", ttn_ref)', np_service)
        self.assertIn('quote(str(track_id), safe="")', rz_api)
        self.assertIn("def _validated_track_id", rz_service)

    def test_rozetka_delivery_contract_is_wired_for_production(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        service = (INTEGRATIONS / "shipment" / "rozetka_delivery" / "service.py").read_text(
            encoding="utf-8"
        )
        api = (INTEGRATIONS / "shipment" / "rozetka_delivery" / "api.py").read_text(encoding="utf-8")
        operations = (INTEGRATIONS / "utils" / "operations.py").read_text(encoding="utf-8")
        logger = (INTEGRATIONS / "utils" / "logger.py").read_text(encoding="utf-8")
        self.assertIn("rozetka_delivery.scheduler.sync_track_statuses", hooks)
        self.assertIn('payload={"data": data}', api)
        self.assertIn('"api/track/status", params={"id": track_ids}', api)
        self.assertIn('"api/track/label"', api)
        self.assertIn('integration="rozetka_delivery"', service)
        self.assertIn('@frappe.whitelist(methods=["POST"])', service)
        self.assertIn('"rozetka_delivery"', operations)
        self.assertIn('"api_token"', logger)

    def test_customer_identification_is_authorized_and_does_not_disclose_pii_early(self):
        service = (INTEGRATIONS / "customer_identification" / "service.py").read_text(
            encoding="utf-8"
        )
        settings = json.loads(
            (
                UI_MODULE
                / "doctype"
                / "customer_identification_settings"
                / "customer_identification_settings.json"
            ).read_text(encoding="utf-8")
        )
        workspace = json.loads(
            (
                UI_MODULE
                / "workspace"
                / "ukrainian_integrations"
                / "ukrainian_integrations.json"
            ).read_text(encoding="utf-8")
        )
        telegram = (INTEGRATIONS / "customer_identification" / "telegram.py").read_text(
            encoding="utf-8"
        )
        telegram_client = (
            INTEGRATIONS / "communication" / "telegram" / "client.py"
        ).read_text(encoding="utf-8")
        birthday = (INTEGRATIONS / "customer_identification" / "birthday.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("IDENTIFICATION_ROLES", service)
        self.assertIn("POS Cashier", service)
        self.assertIn("def begin_pos(", service)
        self.assertIn("_select_channel(settings, channel, for_pos=True)", service)
        self.assertIn('if doc.status == "Verified" else None', service)
        self.assertIn('idempotency_key=sms_key', service)
        self.assertIn("SELECT name FROM `tabCustomer Identification Request`", service)
        self.assertIn("telegram_webhook_secret", telegram)
        self.assertIn("secrets_equal", telegram)
        self.assertIn('"allow_redirects": False', telegram_client)
        self.assertIn('_ALLOWED_METHODS = frozenset({"sendDocument", "sendMessage"})', telegram_client)
        self.assertIn('log.status = "Unknown"', birthday)
        fields = {field["fieldname"]: field for field in settings["fields"]}
        self.assertEqual(fields["default_channel"]["default"], "SMS")
        self.assertEqual(fields["pos_channel"]["default"], "SMS")
        self.assertEqual(fields["allow_pos_channel_selection"]["default"], "0")
        self.assertTrue(
            any(
                link.get("label") == "Identification Channel Settings"
                and link.get("link_to") == "Customer Identification Settings"
                for link in workspace["links"]
            )
        )

    def test_customer_telegram_link_doctype_is_shipped_with_required_fields(self):
        path = (
            UI_MODULE
            / "doctype"
            / "customer_telegram_link"
            / "customer_telegram_link.json"
        )
        self.assertTrue(path.is_file())
        definition = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(definition.get("autoname"), "hash")
        self.assertEqual(definition.get("title_field"), "customer")
        fieldnames = {field["fieldname"] for field in definition["fields"]}
        required_fields = {
            "customer",
            "chat_id",
            "telegram_user_id",
            "status",
            "verification_count",
            "last_verified_at",
            "stop_reason",
        }
        self.assertTrue(required_fields.issubset(fieldnames), required_fields - fieldnames)

    def test_customer_telegram_link_hooks_are_wired(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"Customer": {', hooks)
        self.assertIn("telegram_link.on_customer_insert", hooks)
        self.assertIn('"Customer Telegram Link"', hooks)

    def test_telegram_bot_supports_standalone_commands_and_push_methods(self):
        telegram = (INTEGRATIONS / "customer_identification" / "telegram.py").read_text(
            encoding="utf-8"
        )
        for method in ("sendMessage", "answerCallbackQuery", "editMessageText"):
            self.assertIn(f'"{method}"', telegram)
        for command in ("/start", "/stop", "/status"):
            self.assertIn(f'text == "{command}"', telegram)
        self.assertIn("request_contact", telegram)
        self.assertIn("_handle_callback_query", telegram)
        self.assertIn("_handle_welcome_start", telegram)
        self.assertIn("_handle_stop", telegram)

    def test_customer_telegram_link_is_used_by_quick_create(self):
        service = (INTEGRATIONS / "customer_identification" / "service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "from erpnext_ua.integrations.customer_identification.telegram_link", service
        )
        self.assertIn("ensure_telegram_link(", service)

    def test_telegram_channel_uses_v16_notification_extension_and_no_guest_document_url(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        service = (INTEGRATIONS / "communication" / "telegram" / "service.py").read_text(
            encoding="utf-8"
        )
        profile_path = (
            UI_MODULE
            / "doctype"
            / "telegram_bot_profile"
            / "telegram_bot_profile.json"
        )
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        token = next(field for field in profile["fields"] if field["fieldname"] == "bot_token")
        permissions = {(row["role"], int(row.get("permlevel", 0))) for row in profile["permissions"]}

        self.assertIn("extend_doctype_class", hooks)
        self.assertIn("TelegramNotificationMixin", hooks)
        self.assertEqual(token["fieldtype"], "Password")
        self.assertEqual(token["permlevel"], 1)
        self.assertEqual(token["no_copy"], 1)
        self.assertIn(("System Manager", 1), permissions)
        self.assertNotIn(("Sales Manager", 1), permissions)
        self.assertNotIn(("Sales User", 1), permissions)
        self.assertIn("enqueue_after_commit=True", service)
        self.assertIn("validate_print_permission", service)
        self.assertIn("client.send_document", service)
        self.assertNotIn("allow_guest=True", service)
        self.assertNotIn("get_url", service)

    def test_sensitive_logs_have_partition_or_manager_only_access(self):
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"VitalPBX Call Log"', hooks)
        self.assertIn('"UA Integration Operation"', hooks)

    def test_idempotency_insert_only_recovers_the_unique_key_race(self):
        source = (INTEGRATIONS / "utils" / "operations.py").read_text(encoding="utf-8")
        reserve_source = source[source.index("def reserve_operation") : source.index("def mark_operation")]
        self.assertIn("except frappe.DuplicateEntryError:", source)
        self.assertNotIn("except Exception:", reserve_source)

    def test_all_persisted_log_text_is_sanitized_and_bounded(self):
        source = (INTEGRATIONS / "utils" / "logger.py").read_text(encoding="utf-8")
        self.assertIn('"message": sanitize_text(message)[:1000]', source)
        self.assertIn('"error_trace": _dump(error_trace)', source)


if __name__ == "__main__":
    unittest.main()
