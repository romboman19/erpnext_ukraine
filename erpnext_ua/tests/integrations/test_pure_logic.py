from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import Mock, patch

import requests

try:
    import frappe  # noqa: F401
except ModuleNotFoundError:
    frappe = types.ModuleType("frappe")
    frappe._ = lambda value: value
    frappe.conf = {}
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.throw = lambda message, exc=None: (_ for _ in ()).throw((exc or Exception)(message))
    frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
    frappe.utils = types.SimpleNamespace()
    frappe.db = types.SimpleNamespace(exists=lambda *args, **kwargs: False)
    sys.modules["frappe"] = frappe

from erpnext_ua.integrations.communication.telegram.client import (
    TelegramAPIError as ClientTelegramAPIError,
)
from erpnext_ua.integrations.communication.telegram.client import TelegramClient, is_valid_chat_id
from erpnext_ua.integrations.customer_identification.service import _select_channel
from erpnext_ua.integrations.customer_identification.telegram import (
    TelegramAPIError,
    _telegram,
)
from erpnext_ua.ecommerce.core.exchange import (
    build_canonical_catalog,
    build_yml_catalog,
    parse_orders_csv,
    parse_orders_xml,
)
from erpnext_ua.ecommerce.providers.prom_ua.api import PromUAClient
from erpnext_ua.ecommerce.providers.prom_ua.service import _order_item_rows
from erpnext_ua.ecommerce.providers.shop_express.api import ShopExpressClient
from erpnext_ua.ecommerce.providers.shop_express.service import _batch_warnings, _category_path
from erpnext_ua.integrations.migrations import _merge_app_icons_into_layout, remove_legacy_integration_artifacts
from erpnext_ua.integrations.payments.liqpay.client import LiqPayClient
from erpnext_ua.integrations.payments.monobank.client import MonobankClient
from erpnext_ua.integrations.payments.privatbank.service import _normalize_amount, _pagination_state
from erpnext_ua.integrations.pbx_sms.sms.turbosms import (
    classify_send_response,
    configured_sender_names,
    resolve_configured_sender,
    successful_message_ids,
)
from erpnext_ua.integrations.pbx_sms.vitalpbx.custom_fields import _preserve_upgrade_stable_fieldtypes
from erpnext_ua.integrations.pbx_sms.vitalpbx.events import is_status_transition_allowed
from erpnext_ua.integrations.pbx_sms.vitalpbx.service import PBXRejectedError, _assert_call_accepted
from erpnext_ua.integrations.shipment.rozetka_delivery.api import RZDeliveryClient
from erpnext_ua.integrations.shipment.rozetka_delivery.service import (
    _explicit_rejection as _rz_explicit_rejection,
)
from erpnext_ua.integrations.shipment.rozetka_delivery.service import (
    _provider_payload as _rz_provider_payload,
)
from erpnext_ua.integrations.shipment.rozetka_delivery.service import (
    _recipient_data as _rz_recipient_data,
)
from erpnext_ua.integrations.shipment.rozetka_delivery.service import (
    _shipment_parameters as _rz_shipment_parameters,
)
from erpnext_ua.integrations.shipment.rozetka_delivery.service import (
    _status_rows as _rz_status_rows,
)
from erpnext_ua.integrations.shipment.ukr_poshta.service import _shipment_parameters
from erpnext_ua.integrations.utils.logger import sanitize_payload, sanitize_text
from erpnext_ua.integrations.utils.operations import _assert_same_request, canonical_hash, mark_operation
from erpnext_ua.integrations.utils.security import secrets_equal


class PureLogicTest(unittest.TestCase):
    def test_clean_yml_catalog_escapes_values_and_keeps_stable_skus(self):
        content = build_yml_catalog(
            channel_name="Магазин & сервіс",
            company="HUNTER",
            store_url="https://shop.example.ua",
            currency="UAH",
            categories=[{"id": "1", "name": "Одяг & взуття", "parent_id": ""}],
            products=[
                {
                    "external_id": "SKU-1",
                    "sku": "SKU-1",
                    "name": "Товар <1>",
                    "category_id": "1",
                    "price": 125.5,
                    "quantity": 3,
                    "available": True,
                    "currency": "UAH",
                    "pictures": [],
                }
            ],
        )
        self.assertIn(b"<vendorCode>SKU-1</vendorCode>", content)
        self.assertIn(b"125.50", content)
        self.assertIn(b"\xd0\x9c\xd0\xb0\xd0\xb3\xd0\xb0\xd0\xb7\xd0\xb8\xd0\xbd &amp;", content)

    def test_canonical_catalog_has_explicit_version_and_stock(self):
        content = build_canonical_catalog(
            channel_name="ocStore",
            currency="UAH",
            categories=[{"id": "1", "name": "All", "parent_id": ""}],
            products=[
                {
                    "external_id": "42",
                    "sku": "SKU-42",
                    "name": "Item",
                    "category_id": "1",
                    "price": 10,
                    "quantity": 2,
                    "available": True,
                    "currency": "UAH",
                    "pictures": [],
                }
            ],
        )
        self.assertIn(b'schema="erpnext-ecommerce-v1"', content)
        self.assertIn(b'<quantity>2</quantity>', content)

    def test_order_xml_parser_accepts_clean_exchange_and_rejects_entities(self):
        orders = parse_orders_xml(
            """<?xml version="1.0" encoding="UTF-8"?>
            <ecommerce_exchange schema="erpnext-ecommerce-v1" entity="orders">
              <orders><order id="101" number="OC-101" currency="UAH">
                <customer id="7"><name>Іван</name><phone>+380501234567</phone></customer>
                <items><item sku="SKU-1" quantity="2" price="15.50" /></items>
              </order></orders>
            </ecommerce_exchange>"""
        )
        self.assertEqual(orders[0]["external_id"], "101")
        self.assertEqual(orders[0]["customer"]["name"], "Іван")
        self.assertEqual(orders[0]["items"][0]["sku"], "SKU-1")
        with self.assertRaises(ValueError):
            parse_orders_xml('<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><order id="1"/>')
        with self.assertRaises(ValueError):
            parse_orders_xml((" " * 5000) + '<!DOCTYPE foo><order id="1"/>')

    def test_order_csv_parser_groups_product_rows_by_order(self):
        orders = parse_orders_csv(
            "order_id,order_number,customer_name,sku,quantity,price,currency\n"
            "1,OC-1,Іван,SKU-1,1,10,UAH\n"
            "1,OC-1,Іван,SKU-2,2,20,UAH\n"
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(orders[0]["items"]), 2)

    @patch("erpnext_ua.ecommerce.providers.shop_express.api.requests.post")
    def test_shop_express_reauthenticates_once_after_expired_token(self, post):
        def response(payload):
            raw = __import__("json").dumps(payload).encode()
            result = Mock(status_code=200, headers={"Content-Length": str(len(raw))})
            result.iter_content.return_value = [raw]
            return result

        post.side_effect = [
            response({"status": "OK", "response": {"token": "first"}}),
            response({"status": "UNAUTHORIZED", "response": {}}),
            response({"status": "OK", "response": {"token": "second"}}),
            response({"status": "OK", "response": {"orders": []}}),
        ]
        stored = []
        client = ShopExpressClient(
            base_url="https://shop.example.ua",
            login="api@example.ua",
            password="secret",
            token_callback=stored.append,
        )
        result = client.export_orders(limit=10)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(post.call_count, 4)
        self.assertEqual(stored, ["first", "second"])
        self.assertEqual(post.call_args.kwargs["json"]["token"], "second")

    @patch("erpnext_ua.ecommerce.providers.shop_express.api.requests.post")
    def test_shop_express_does_not_retry_ambiguous_timeout(self, post):
        post.side_effect = requests.Timeout("unknown outcome")
        client = ShopExpressClient(
            base_url="https://shop.example.ua",
            login="api@example.ua",
            password="secret",
            token="cached",
        )
        with self.assertRaises(requests.Timeout):
            client.update_residues([{"sku": "SKU-1", "residues": 1}])
        self.assertEqual(post.call_count, 1)

    def test_shop_express_batch_log_rejects_partial_or_failed_rows(self):
        payload = [{"sku": "SKU-1"}, {"sku": "SKU-2"}]
        response = {
            "status": "OK",
            "response": {"log": [{"status": "OK"}, {"status": "ERROR", "sku": "SKU-2"}]},
        }
        self.assertEqual(_batch_warnings(response, payload, "stock update")[0]["sku"], "SKU-2")
        with self.assertRaises(RuntimeError):
            _batch_warnings({"status": "OK", "response": {"log": []}}, payload, "stock update")

    def test_shop_express_catalog_category_path_uses_stable_external_ids(self):
        categories = {
            "1": {"id": "1", "name_key": "All Item Groups", "name": "All", "parent_id": ""},
            "2": {"id": "2", "name_key": "Clothes", "name": "Одяг", "parent_id": "1"},
        }
        self.assertEqual(
            _category_path("2", categories),
            [
                {"external_id": "All Item Groups", "value": {"uk": "All"}},
                {
                    "external_id": "Clothes",
                    "parent_external_id": "All Item Groups",
                    "value": {"uk": "Одяг"},
                },
            ],
        )

    def test_pos_identification_defaults_to_locked_turbosms_channel(self):
        settings = types.SimpleNamespace(
            default_channel="Telegram",
            pos_channel="SMS",
            allow_pos_channel_selection=0,
        )
        self.assertEqual(_select_channel(settings, for_pos=True), "SMS")
        self.assertEqual(
            _select_channel(settings, "Telegram", for_pos=True),
            "SMS",
        )
        self.assertEqual(_select_channel(settings), "Telegram")

    def test_pos_channel_selection_requires_explicit_opt_in(self):
        settings = types.SimpleNamespace(
            default_channel="SMS",
            pos_channel="SMS",
            allow_pos_channel_selection=1,
        )
        self.assertEqual(
            _select_channel(settings, "Telegram", for_pos=True),
            "Telegram",
        )
        self.assertEqual(
            _select_channel(settings, "TurboSMS", for_pos=True),
            "SMS",
        )

    def test_new_apps_are_merged_into_saved_desktop_layout_without_reset(self):
        layout = [
            {"label": "Framework", "idx": 0},
            {"label": "Ukrainian Integrations", "idx": 1, "hidden": 1},
        ]
        app_icons = [
            {"label": "Ukrainian Integrations", "idx": 10},
            {"label": "ERPNext Ukraine", "idx": 11},
            {"label": "Print Designer", "idx": 12},
        ]

        merged, added = _merge_app_icons_into_layout(layout, app_icons)

        self.assertEqual(added, 2)
        self.assertEqual(
            [icon["label"] for icon in merged],
            ["Framework", "Ukrainian Integrations", "ERPNext Ukraine", "Print Designer"],
        )
        self.assertEqual(merged[1]["hidden"], 1)
        self.assertEqual(
            layout,
            [
                {"label": "Framework", "idx": 0},
                {"label": "Ukrainian Integrations", "idx": 1, "hidden": 1},
            ],
        )

        merged_again, added_again = _merge_app_icons_into_layout(merged, app_icons)
        self.assertEqual(added_again, 0)
        self.assertEqual(merged_again, merged)

    def test_legacy_integration_cleanup_migrates_turbosms_sender_before_deletion(self):
        legacy_doctypes = {
            "NP Integration Settings",
            "UP Integration Settings",
            "TurboSMS Settings",
        }
        database = Mock()
        database.exists.side_effect = lambda doctype, name: (
            doctype == "DocType" and name in legacy_doctypes
        )
        database.sql.return_value = [("HUNTER RV",)]
        database.get_value.return_value = 0
        settings = Mock()
        settings.get.return_value = []

        with (
            patch.object(frappe, "db", database),
            patch.object(frappe, "get_single", return_value=settings, create=True),
            patch.object(frappe, "delete_doc", create=True) as delete_doc,
            patch.object(frappe, "clear_cache", create=True),
        ):
            result = remove_legacy_integration_artifacts()

        self.assertEqual(
            result["removed_doctypes"],
            ["NP Integration Settings", "UP Integration Settings"],
        )
        self.assertTrue(result["migrated_turbosms_sender"])
        settings.append.assert_called_once_with(
            "senders",
            {"sender_name": "HUNTER RV", "is_active": 1, "is_default": 1},
        )
        settings.save.assert_called_once_with(ignore_permissions=True)
        self.assertEqual(delete_doc.call_count, 2)
        database.delete.assert_called_once_with(
            "Singles",
            {"doctype": "TurboSMS Settings", "field": "sender"},
        )
        database.sql.assert_called_once_with(
            unittest.mock.ANY,
            ("TurboSMS Settings", "sender"),
        )

    def test_legacy_sender_profile_fieldtype_is_not_coerced(self):
        custom_fields = {
            "Sales Invoice": [
                {
                    "fieldname": "np_sender_profile",
                    "fieldtype": "Link",
                    "options": "NP Sender Profile",
                }
            ]
        }

        def get_existing(*args, **kwargs):
            return {"fieldtype": "Data", "options": None}

        _preserve_upgrade_stable_fieldtypes(custom_fields, get_existing=get_existing)
        field = custom_fields["Sales Invoice"][0]
        self.assertEqual(field["fieldtype"], "Data")
        self.assertNotIn("options", field)

    def test_legacy_sender_cleanup_accepts_null_single_value(self):
        database = Mock()
        database.exists.side_effect = lambda doctype, name: (
            doctype == "DocType" and name == "TurboSMS Settings"
        )
        database.sql.return_value = [(None,)]
        database.get_value.return_value = 0
        settings = Mock()
        settings.get.return_value = []

        with (
            patch.object(frappe, "db", database),
            patch.object(frappe, "get_single", return_value=settings, create=True),
            patch.object(frappe, "clear_cache", create=True),
        ):
            result = remove_legacy_integration_artifacts()

        self.assertFalse(result["migrated_turbosms_sender"])
        settings.append.assert_not_called()

    def test_payload_redaction_is_recursive(self):
        payload = {"token": "secret", "nested": [{"private_key": "private", "value": 4}]}
        self.assertEqual(
            sanitize_payload(payload),
            {"token": "***REDACTED***", "nested": [{"private_key": "***REDACTED***", "value": 4}]},
        )

    def test_traceback_and_url_secrets_are_redacted(self):
        raw = (
            "GET https://example.test/path?token=abc123&x=1 "
            "Authorization: Bearer secret-value "
            "https://api.telegram.org/bot123456:telegram-secret/sendMessage "
            "{'bot_token': 'another-secret'}"
        )
        sanitized = sanitize_text(raw)
        self.assertNotIn("abc123", sanitized)
        self.assertNotIn("secret-value", sanitized)
        self.assertNotIn("telegram-secret", sanitized)
        self.assertNotIn("another-secret", sanitized)

    def test_canonical_hash_is_order_independent(self):
        self.assertEqual(canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1}))
        self.assertNotEqual(canonical_hash({"a": 1}), canonical_hash({"a": 2}))

    def test_idempotency_key_cannot_change_payload(self):
        doc = types.SimpleNamespace(request_hash=canonical_hash({"amount": 10}))
        _assert_same_request(doc, {"amount": 10})
        with self.assertRaises(frappe.ValidationError):
            _assert_same_request(doc, {"amount": 11})

    def test_non_terminal_operation_clears_completed_timestamp(self):
        doc = types.SimpleNamespace(
            status="failed",
            completed_at="old",
            external_id="",
            response_payload="",
            last_error="old",
            save=lambda **kwargs: None,
        )
        mark_operation(doc, "started", durable=False)
        self.assertIsNone(doc.completed_at)

    def test_constant_time_secret_comparison_contract(self):
        self.assertTrue(secrets_equal("same", "same"))
        self.assertFalse(secrets_equal("same", "different"))

    def test_liqpay_v7_signatures_are_deterministic(self):
        client = LiqPayClient("public", "private")
        signed = client.cnb_form_payload({"version": 7, "amount": 10, "currency": "UAH"})
        self.assertEqual(signed["signature"], client.make_signature(signed["data"], 7))
        self.assertNotEqual(
            signed["signature"], LiqPayClient("public", "other").make_signature(signed["data"], 7)
        )

    def test_liqpay_v7_matches_official_signature_vector(self):
        data = (
            "eyJwdWJsaWNfa2V5IjoiaTAwMDAwMDAwIiwidmVyc2lvbiI6NywiYWN0aW9uIjoicGF5IiwiYW1vdW50IjoiMyIs"
            "ImN1cnJlbmN5IjoiVUFIIiwiZGVzY3JpcHRpb24iOiJ0ZXN0Iiwib3JkZXJfaWQiOiIwMDAwMDEifQ=="
        )
        client = LiqPayClient("i00000000", "a4825234f4bae72a0be04eafe9e8e2bada209255")
        self.assertEqual(
            client.make_signature(data, 7),
            "0adgJ8F2Ds5HCVkcz4AlmdLMRoIJf7IxsL3QmeFRz/s=",
        )

    def test_liqpay_v3_callback_compatibility_is_explicit(self):
        client = LiqPayClient("public", "private")
        signed = client.cnb_form_payload({"version": 3, "amount": 10, "currency": "UAH"})
        self.assertEqual(signed["signature"], client.make_signature(signed["data"], 3))
        self.assertNotEqual(signed["signature"], client.make_signature(signed["data"], 7))
        with self.assertRaises(ValueError):
            client.cnb_form_payload({"version": 6})

    def test_privatbank_amount_modes(self):
        self.assertEqual(_normalize_amount("12345", 1), 123.45)
        self.assertEqual(_normalize_amount("123.45", 0), 123.45)
        self.assertEqual(_normalize_amount(0, 0), 0.0)

    def test_privatbank_pagination_variants(self):
        self.assertEqual(_pagination_state({"exist_next_page": True, "next_page_id": "abc"}), (True, "abc"))
        self.assertEqual(_pagination_state({"hasNextPage": "false"}), (False, None))
        self.assertEqual(_pagination_state({"existNextPage": "1", "nextPageId": 42}), (True, "42"))

    def test_privatbank_rejects_non_finite_amount(self):
        with self.assertRaises(ValueError):
            _normalize_amount("nan", 1)

    @patch("erpnext_ua.integrations.payments.monobank.client.requests.get")
    def test_monobank_rejects_non_list_statement_payload(self, get):
        response = Mock(status_code=200, text='{"error":"unexpected"}')
        response.json.return_value = {"error": "unexpected"}
        get.return_value = response
        with self.assertRaises(ValueError):
            MonobankClient("token").statements("account", 1, 2)

    @patch("erpnext_ua.ecommerce.providers.prom_ua.api.requests.post")
    def test_prom_stock_uses_external_id_contract(self, post):
        response = Mock(status_code=200, text='{"processed_ids":[1],"errors":{}}')
        response.json.return_value = {"processed_ids": [1], "errors": {}}
        post.return_value = response
        row = {"id": "SKU-1", "quantity_in_stock": 3, "presence": "available"}
        PromUAClient("token").update_stock([row])
        _, kwargs = post.call_args
        self.assertTrue(post.call_args.args[0].endswith("/products/edit_by_external_id"))
        self.assertEqual(kwargs["json"], [row])

    @patch("erpnext_ua.ecommerce.providers.prom_ua.api.requests.get")
    def test_prom_order_pagination_uses_last_id(self, get):
        response = Mock(status_code=200, text='{"orders":[]}')
        response.json.return_value = {"orders": []}
        get.return_value = response
        PromUAClient("token").list_orders(limit=50, last_id=123)
        self.assertEqual(get.call_args.kwargs["params"], {"limit": 50, "last_id": 123})

    @patch("erpnext_ua.ecommerce.providers.prom_ua.service.frappe.db.exists")
    def test_prom_order_never_silently_imports_partial_items(self, exists):
        exists.side_effect = [True, False]
        products = [
            {"external_id": "SKU-OK", "quantity": 1, "price": 10},
            {"external_id": "SKU-MISSING", "quantity": 1, "price": 20},
        ]
        with self.assertRaises(ValueError):
            _order_item_rows({"products": products})

    @patch("erpnext_ua.ecommerce.providers.prom_ua.service.frappe.db.exists", return_value=True)
    def test_prom_order_rejects_non_finite_values(self, _exists):
        with self.assertRaises(ValueError):
            _order_item_rows(
                {"products": [{"external_id": "SKU-1", "quantity": "nan", "price": 10}]}
            )

    def test_turbosms_requires_provider_and_recipient_success(self):
        valid = {
            "response_code": 0,
            "response_status": "OK",
            "response_result": [
                {"response_code": 0, "response_status": "OK", "message_id": "m-1"}
            ],
        }
        self.assertEqual(successful_message_ids(valid), ["m-1"])
        self.assertEqual(successful_message_ids({**valid, "response_code": 103}), [])
        rejected = {**valid, "response_result": [{"response_code": 212, "message_id": None}]}
        self.assertEqual(successful_message_ids(rejected), [])
        self.assertEqual(classify_send_response(rejected)[0], "failed")
        malformed = {**valid, "response_result": [{"message_id": "m-1"}]}
        self.assertEqual(classify_send_response(malformed)[0], "unknown")

    def test_turbosms_accepts_documented_message_success_codes(self):
        recipient = {
            "response_code": 0,
            "response_status": "OK",
            "message_id": "m-801",
        }
        statuses = {
            800: "SUCCESS_MESSAGE_ACCEPTED",
            801: "SUCCESS_MESSAGE_SENT",
            802: "SUCCESS_MESSAGE_PARTIAL_ACCEPTED",
            803: "SUCCESS_MESSAGE_PARTIAL_SENT",
        }
        for response_code, response_status in statuses.items():
            with self.subTest(response_code=response_code):
                data = {
                    "response_code": response_code,
                    "response_status": response_status,
                    "response_result": [recipient],
                }
                self.assertEqual(classify_send_response(data), ("succeeded", ["m-801"]))

    def test_turbosms_success_code_still_requires_matching_status_and_recipient_success(self):
        valid = {
            "response_code": 801,
            "response_status": "SUCCESS_MESSAGE_SENT",
            "response_result": [
                {"response_code": 0, "response_status": "OK", "message_id": "m-1"}
            ],
        }
        self.assertEqual(
            classify_send_response({**valid, "response_status": "OK"})[0],
            "unknown",
        )
        self.assertEqual(
            classify_send_response(
                {
                    **valid,
                    "response_result": [
                        {
                            "response_code": 503,
                            "response_status": "FAILED_SMS_SEND",
                            "message_id": None,
                        }
                    ],
                }
            )[0],
            "failed",
        )

    def test_turbosms_sender_registry_is_shared_and_fail_closed(self):
        cfg = {
            "enabled": 1,
            "sender": "Primary",
            "senders": [
                {"sender_name": "Primary", "is_active": 1},
                {"sender_name": "Retail", "is_active": 1},
                {"sender_name": "primary", "is_active": 1},
            ],
        }
        self.assertEqual(configured_sender_names(cfg), ["Primary", "Retail"])
        self.assertEqual(resolve_configured_sender(cfg=cfg), "Primary")
        self.assertEqual(resolve_configured_sender("retail", cfg), "Retail")
        with self.assertRaises(ValueError):
            resolve_configured_sender("Free form sender", cfg)

    def test_turbosms_legacy_default_remains_a_local_sender(self):
        cfg = {"enabled": 1, "sender": "Legacy", "senders": []}
        self.assertEqual(configured_sender_names(cfg), ["Legacy"])
        self.assertEqual(resolve_configured_sender(cfg=cfg), "Legacy")

    def test_vitalpbx_statuses_do_not_regress(self):
        self.assertTrue(is_status_transition_allowed("ringing", "answered"))
        self.assertTrue(is_status_transition_allowed("answered", "completed"))
        self.assertFalse(is_status_transition_allowed("completed", "ringing"))
        self.assertFalse(is_status_transition_allowed("missed", "failed"))

    def test_vitalpbx_requires_explicit_call_acceptance(self):
        _assert_call_accepted({"success": True})
        with self.assertRaises(PBXRejectedError):
            _assert_call_accepted({"success": False, "error": "rejected"})
        with self.assertRaises(RuntimeError):
            _assert_call_accepted({"message": "received"})

    @patch("erpnext_ua.integrations.customer_identification.telegram.get_enabled_bot_profile")
    @patch("erpnext_ua.integrations.customer_identification.telegram._settings")
    @patch("erpnext_ua.integrations.customer_identification.telegram.requests.post")
    def test_telegram_client_rejects_redirects_and_bounds_requests(self, post, settings, get_profile):
        get_profile.return_value.get_password.return_value = "123456:abcdefghijklmnopqrstuvwxyzABCDE_1234"
        raw = b'{"ok":true,"result":{"message_id":1}}'
        response = Mock(status_code=200, headers={"Content-Length": str(len(raw))})
        response.iter_content.return_value = [raw]
        post.return_value = response
        self.assertTrue(_telegram("sendMessage", {"chat_id": "1", "text": "test"})["ok"])
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 20))

    @patch("erpnext_ua.integrations.customer_identification.telegram.get_enabled_bot_profile")
    @patch("erpnext_ua.integrations.customer_identification.telegram._settings")
    @patch("erpnext_ua.integrations.customer_identification.telegram.requests.post")
    def test_telegram_server_error_is_ambiguous(self, post, settings, get_profile):
        get_profile.return_value.get_password.return_value = "123456:abcdefghijklmnopqrstuvwxyzABCDE_1234"
        raw = b'{"ok":false}'
        response = Mock(status_code=503, headers={"Content-Length": str(len(raw))})
        response.iter_content.return_value = [raw]
        post.return_value = response
        with self.assertRaises(TelegramAPIError) as raised:
            _telegram("sendMessage", {"chat_id": "1", "text": "test"})
        self.assertFalse(raised.exception.definite)

    def test_telegram_chat_ids_are_strictly_numeric(self):
        self.assertTrue(is_valid_chat_id("123456"))
        self.assertTrue(is_valid_chat_id("-1001234567890"))
        self.assertFalse(is_valid_chat_id("@channel"))
        self.assertFalse(is_valid_chat_id("12 34"))
        self.assertFalse(is_valid_chat_id(""))

    def test_telegram_pdf_is_uploaded_directly_without_a_public_url(self):
        raw = b'{"ok":true,"result":{"message_id":7}}'
        response = Mock(status_code=200, headers={"Content-Length": str(len(raw))})
        response.iter_content.return_value = [raw]
        post = Mock(return_value=response)

        data = TelegramClient(
            "123456:abcdefghijklmnopqrstuvwxyzABCDE_1234",
            post=post,
        ).send_document(
            chat_id="-100123",
            content=b"%PDF-test",
            filename="invoice.pdf",
            caption="Invoice",
        )

        self.assertTrue(data["ok"])
        self.assertNotIn("json", post.call_args.kwargs)
        self.assertEqual(post.call_args.kwargs["data"]["chat_id"], "-100123")
        self.assertEqual(post.call_args.kwargs["files"]["document"][0], "invoice.pdf")

    def test_telegram_client_rejects_unapproved_methods(self):
        with self.assertRaises(ValueError):
            TelegramClient("123456:abcdefghijklmnopqrstuvwxyzABCDE_1234").request("getFile", {})

    def test_telegram_client_uses_provider_error_code_for_definite_rejection(self):
        raw = b'{"ok":false,"error_code":400}'
        response = Mock(status_code=200, headers={"Content-Length": str(len(raw))})
        response.iter_content.return_value = [raw]
        post = Mock(return_value=response)
        client = TelegramClient("123456:abcdefghijklmnopqrstuvwxyzABCDE_1234", post=post)

        with self.assertRaises(ClientTelegramAPIError) as raised:
            client.send_message(chat_id="123", text="test")

        self.assertTrue(raised.exception.definite)

    def test_ukrposhta_ui_weight_is_converted_from_kg_to_grams(self):
        values = _shipment_parameters({"weight": 1.25}, default_declared_value=100)
        self.assertEqual(values["weight"], 1250)
        self.assertEqual(values["declared_value"], 100)

    def test_rozetka_delivery_payload_matches_official_create_dto(self):
        city_id = "7c042552-869c-4975-9435-694d0de1591e"
        department_id = "d91a2ddc-a512-4eb3-8fe8-fbdd04116ae3"
        recipient = _rz_recipient_data(
            city_id=city_id,
            department_id=department_id,
            first_name="Іван",
            middle_name="Іванович",
            last_name="Іванов",
            phone="+380671234567",
        )
        shipment = _rz_shipment_parameters(
            {
                "weight": 1.25,
                "length": 15,
                "width": 25,
                "height": 30,
                "places": 1,
                "insurance_cost": 500,
                "cost": 300,
                "delivery_payer": "sender",
            },
            default_insurance_cost=1,
        )
        profile = {
            "sender_city_id": city_id,
            "sender_department_id": department_id,
            "sender_first_name": "Петро",
            "sender_middle_name": "Петрович",
            "sender_last_name": "Петренко",
            "sender_phone": "380501234567",
            "carrier_id": "",
        }
        payload = _rz_provider_payload(
            profile=profile,
            recipient=recipient,
            shipment=shipment,
            visible_id="ACC-SINV-2026-00001",
            description="Замовлення",
        )
        self.assertEqual(
            set(payload),
            {
                "visible_id",
                "description",
                "type",
                "places",
                "delivery_payer",
                "cost",
                "insurance_cost",
                "params",
                "sender",
                "recipient",
            },
        )
        self.assertEqual(payload["type"], "dept-dept")
        self.assertEqual(payload["params"]["weight"], 1.25)
        self.assertEqual(payload["recipient"]["phone"], ["380671234567"])

    def test_rozetka_delivery_rejects_cod_above_insured_value(self):
        with patch(
            "erpnext_ua.integrations.shipment.rozetka_delivery.service.frappe.throw",
            side_effect=ValueError,
        ), self.assertRaises(ValueError):
            _rz_shipment_parameters(
                {"insurance_cost": 100, "cost": 101},
                default_insurance_cost=100,
            )

    @patch("erpnext_ua.integrations.shipment.rozetka_delivery.api.requests.request")
    def test_rozetka_delivery_create_uses_data_wrapper_and_no_retry(self, request):
        raw = b'{"statusCode":0,"data":{"track_id":"101123456789"}}'
        response = Mock(status_code=201, headers={"Content-Length": str(len(raw))})
        response.iter_content.return_value = [raw]
        request.return_value = response
        payload = {"type": "dept-dept"}
        result = RZDeliveryClient("token").create_track(payload)
        self.assertEqual(result["data"]["track_id"], "101123456789")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.kwargs["json"], {"data": payload})
        self.assertEqual(request.call_args.kwargs["timeout"], (10, 40))
        self.assertTrue(request.call_args.kwargs["stream"])
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    @patch("erpnext_ua.integrations.shipment.rozetka_delivery.api.requests.request")
    def test_rozetka_delivery_timeout_is_not_retried(self, request):
        request.side_effect = requests.Timeout("lost response")
        with self.assertRaises(requests.Timeout):
            RZDeliveryClient("token").create_track({"type": "dept-dept"})
        self.assertEqual(request.call_count, 1)

    @patch("erpnext_ua.integrations.shipment.rozetka_delivery.api.requests.request")
    def test_rozetka_delivery_directory_booleans_are_lowercase(self, request):
        raw = b'{"statusCode":0,"data":[]}'
        response = Mock(status_code=200, headers={"Content-Length": str(len(raw))})
        response.iter_content.return_value = [raw]
        request.return_value = response
        RZDeliveryClient().search_departments(
            "b205dde2-2e2e-4eb9-aef2-a67c82bbdf27",
            can_receive_tracks=False,
            can_give_out_tracks=True,
        )
        self.assertEqual(request.call_args.kwargs["params"]["can_receive_tracks"], "false")
        self.assertEqual(request.call_args.kwargs["params"]["can_give_out_tracks"], "true")

    def test_rozetka_delivery_status_shape_supports_single_and_list_responses(self):
        row = {"track_id": "101123456789", "last_status": {"status": "planned"}}
        self.assertEqual(_rz_status_rows({"data": row}), [row])
        self.assertEqual(_rz_status_rows({"data": [row]}), [row])

    def test_rozetka_delivery_only_explicit_client_rejections_are_definite_failures(self):
        rejected_response = Mock(status_code=400)
        rejected = requests.HTTPError("400", response=rejected_response)
        ambiguous_response = Mock(status_code=503)
        ambiguous = requests.HTTPError("503", response=ambiguous_response)
        self.assertTrue(_rz_explicit_rejection(rejected))
        self.assertFalse(_rz_explicit_rejection(ambiguous))

if __name__ == "__main__":
    unittest.main()
