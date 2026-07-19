from __future__ import annotations

import ftplib
import json
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import requests

try:
    import frappe
except ModuleNotFoundError:
    frappe = types.ModuleType("frappe")
    frappe._ = lambda value: value
    frappe.conf = {}
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
    frappe.throw = lambda message, exc=None: (_ for _ in ()).throw((exc or Exception)(message))
    frappe.whitelist = lambda *args, **kwargs: (lambda function: function)
    frappe.utils = types.SimpleNamespace()
    frappe.db = types.SimpleNamespace(exists=lambda *args, **kwargs: False)
    frappe_model = types.ModuleType("frappe.model")
    frappe_document = types.ModuleType("frappe.model.document")
    frappe_document.Document = object
    sys.modules["frappe"] = frappe
    sys.modules["frappe.model"] = frappe_model
    sys.modules["frappe.model.document"] = frappe_document

from ukrainian_integrations.ecommerce.base import orders
from ukrainian_integrations.ecommerce.base.mapping import record_export_hash
from ukrainian_integrations.ecommerce.base.serializers import get_serializer
from ukrainian_integrations.ecommerce.base.serializers.transforms import (
    apply_export_transform,
    register_custom_transform,
)
from ukrainian_integrations.ecommerce.base.transport.ftp import FileDeliveryTransport
from ukrainian_integrations.ecommerce.base.transport.http import (
    AmbiguousTransportError,
    HTTPTransport,
)
from ukrainian_integrations.ecommerce.doctype.ecommerce_file_field.ecommerce_file_field import (
    EcommerceFileField,
)
from ukrainian_integrations.ecommerce.providers.ocstore import service as ocstore_service
from ukrainian_integrations.ecommerce.providers.ocstore.xml_orders import parse_order_file
from ukrainian_integrations.patches.v0_5 import (
    backfill_ecommerce_item_mapping,
    convert_ecommerce_channel_custom_field_to_data,
    move_ecommerce_item_mapping_to_module,
)
from ukrainian_integrations.patches.v0_6 import register_ecommerce_module_and_sync_mapping


@register_custom_transform("ukrainian_integrations.tests.ecommerce.uppercase")
def _uppercase(value):
    return str(value or "").upper()


def _layout(format_name: str):
    return {
        "format": format_name,
        "encoding": "UTF-8",
        "delimiter": ";",
        "root_element": "catalog",
        "item_element": "offer",
        "fields": [
            {
                "erp_fieldname": "item_code",
                "external_column": "sku",
                "transform": "none",
                "required": 1,
            },
            {
                "erp_fieldname": "price",
                "external_column": "price",
                "transform": "number_2dp",
                "required": 1,
            },
        ],
    }


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._content = json.dumps(payload).encode("utf-8")
        self.headers = {"Content-Length": str(len(self._content))}

    @property
    def content(self):
        return self._content

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield self._content

    def json(self):
        return json.loads(self._content)


class _Session:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FTPConnection:
    def __init__(self):
        self.files: dict[str, bytes] = {}

    def size(self, path):
        if path not in self.files:
            raise ftplib.error_perm("550 missing")
        return len(self.files[path])

    def storbinary(self, command, stream):
        self.files[command.removeprefix("STOR ")] = stream.read()

    def retrbinary(self, command, callback):
        callback(self.files[command.removeprefix("RETR ")])

    def rename(self, source, target):
        self.files[target] = self.files.pop(source)

    def delete(self, path):
        del self.files[path]


class EcommerceBaseTest(unittest.TestCase):
    def test_configurable_serializers_round_trip_the_layout(self):
        source = [{"item_code": "SKU-1", "price": 12.5, "internal_note": "not exported"}]
        for format_name in ("CSV", "XML", "YML"):
            with self.subTest(format=format_name):
                serializer = get_serializer(format_name)
                payload = serializer.serialize(source, _layout(format_name))
                restored = serializer.deserialize(payload, _layout(format_name))
                self.assertEqual(restored, [{"item_code": "SKU-1", "price": "12.50"}])

    def test_xml_import_rejects_dtd_and_entities(self):
        serializer = get_serializer("XML")
        with self.assertRaisesRegex(ValueError, "DTD and XML entities"):
            serializer.deserialize(
                b'<!DOCTYPE catalog [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><catalog/>',
                _layout("XML"),
            )

    def test_last_export_hash_is_based_on_the_serialized_layout_payload(self):
        serializer = get_serializer("CSV")
        layout = _layout("CSV")
        baseline = record_export_hash(
            serializer,
            layout,
            {"item_code": "SKU-1", "price": 10, "internal_note": "first"},
        )
        ignored_change = record_export_hash(
            serializer,
            layout,
            {"item_code": "SKU-1", "price": 10, "internal_note": "second"},
        )
        exported_change = record_export_hash(
            serializer,
            layout,
            {"item_code": "SKU-1", "price": 11, "internal_note": "second"},
        )
        self.assertEqual(baseline, ignored_change)
        self.assertNotEqual(baseline, exported_change)

    def test_ocstore_keeps_exact_payload_hashes_separate_per_entity(self):
        records = [{"item": "ITEM-1", "item_code": "SKU-1", "price": 10}]
        product_layout = _layout("XML")
        price_layout = {
            **_layout("XML"),
            "root_element": "prices",
            "fields": [_layout("XML")["fields"][1]],
        }
        configs = [
            types.SimpleNamespace(entity="Products"),
            types.SimpleNamespace(entity="Prices"),
        ]
        hashes = ocstore_service._record_entity_hashes(
            records,
            configs,
            {"Products": product_layout, "Prices": price_layout},
        )["ITEM-1"]
        expected_product = record_export_hash(get_serializer("XML"), product_layout, records[0])
        expected_price = record_export_hash(get_serializer("XML"), price_layout, records[0])
        self.assertEqual(hashes, {"Products": expected_product, "Prices": expected_price})
        self.assertEqual(
            ocstore_service._combined_hash(hashes),
            ocstore_service._combined_hash(dict(reversed(list(hashes.items())))),
        )

    def test_ocstore_manual_actions_report_actionable_disabled_entity_errors(self):
        settings = types.SimpleNamespace(
            name="top-trig",
            sync_entities=[
                types.SimpleNamespace(entity="Products", enabled=0, method="File"),
                types.SimpleNamespace(entity="Orders", enabled=0, method="File"),
            ],
        )
        settings.get = lambda key, default=None: getattr(settings, key, default)
        with (
            patch.object(ocstore_service, "_settings", return_value=settings),
            self.assertRaisesRegex(Exception, "Enable at least one ocStore export entity"),
        ):
            ocstore_service.export_bundle("top-trig", force=True)
        with self.assertRaisesRegex(Exception, "Enable the ocStore Orders import entity"):
            ocstore_service._active_order_config(settings)

    def test_custom_transforms_are_code_registered_and_fail_closed(self):
        self.assertEqual(
            apply_export_transform(
                "товар",
                "custom-method-path",
                "ukrainian_integrations.tests.ecommerce.uppercase",
            ),
            "ТОВАР",
        )
        with self.assertRaisesRegex(ValueError, "not registered"):
            apply_export_transform("value", "custom-method-path", "os.system")

    def test_file_field_controller_rejects_unregistered_custom_method(self):
        field = types.SimpleNamespace(
            erp_fieldname="description",
            external_column="description",
            transform="custom-method-path",
            custom_transform_method="os.system",
        )
        with self.assertRaisesRegex(Exception, "not registered"):
            EcommerceFileField.validate(field)

    def test_mutating_http_requires_a_key_and_never_follows_redirects(self):
        session = _Session(_Response(200, {"ok": True}))
        transport = HTTPTransport(
            base_url="https://api.example.test",
            allowlist_config_key="unused_hosts",
            default_hosts={"api.example.test"},
            session=session,
        )
        with self.assertRaisesRegex(ValueError, "idempotency key"):
            transport.request_json("POST", "/stock", json_payload={"qty": 1})
        result = transport.request_json(
            "POST",
            "/stock",
            json_payload={"qty": 1},
            idempotency_key="stock:SKU-1:1",
        )
        self.assertEqual(result, {"ok": True})
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertEqual(session.calls[0][1]["headers"]["Idempotency-Key"], "stock:SKU-1:1")

    def test_ambiguous_http_mutation_is_not_retried(self):
        for outcome in (requests.Timeout("unknown"), _Response(503, {"error": "later"})):
            with self.subTest(outcome=type(outcome).__name__):
                session = _Session(outcome)
                transport = HTTPTransport(
                    base_url="https://api.example.test",
                    allowlist_config_key="unused_hosts",
                    default_hosts={"api.example.test"},
                    session=session,
                )
                with self.assertRaises(AmbiguousTransportError):
                    transport.request_json(
                        "POST",
                        "/stock",
                        json_payload={"qty": 1},
                        idempotency_key="stock:SKU-1:1",
                    )
                self.assertEqual(len(session.calls), 1)

    def test_file_upload_sidecar_makes_repeats_immutable_and_delete_idempotent(self):
        endpoint = {
            "protocol": "FTP",
            "host": "ftp.example.test",
            "port": 21,
            "username": "erpnext",
            "password": "secret",
            "base_path": "/exchange",
            "passive_mode": 1,
        }
        connection = _FTPConnection()

        @contextmanager
        def connected():
            yield connection

        with patch.object(frappe, "conf", {}):
            transport = FileDeliveryTransport(endpoint)
        transport._connection = connected
        first = transport.upload("catalog.xml", b"one", idempotency_key="catalog:1")
        repeated = transport.upload("catalog.xml", b"one", idempotency_key="catalog:1")
        self.assertFalse(first["idempotent"])
        self.assertTrue(repeated["idempotent"])
        with self.assertRaisesRegex(ValueError, "different file upload"):
            transport.upload("catalog.xml", b"two", idempotency_key="catalog:1")
        self.assertTrue(transport.delete("catalog.xml")["deleted"])
        self.assertFalse(transport.delete("catalog.xml")["deleted"])

    def test_file_delivery_endpoint_authorizes_its_host_without_site_config(self):
        endpoint = {
            "protocol": "SFTP",
            "host": "Files.Example.Test.",
            "port": 22,
            "username": "erpnext",
            "password": "secret",
            "base_path": "/exchange",
        }
        with patch.object(frappe, "conf", {}):
            transport = FileDeliveryTransport(endpoint)
        self.assertEqual(transport.host, "files.example.test")

        endpoint["host"] = "files.example.test:22"
        with self.assertRaisesRegex(Exception, "valid hostname"):
            FileDeliveryTransport(endpoint)

    def test_replaceable_feed_keeps_each_operation_key_immutable(self):
        endpoint = {
            "protocol": "FTP",
            "host": "ftp.example.test",
            "port": 21,
            "username": "erpnext",
            "password": "secret",
            "base_path": "/exchange",
            "passive_mode": 1,
        }
        connection = _FTPConnection()

        @contextmanager
        def connected():
            yield connection

        with patch.object(frappe, "conf", {}):
            transport = FileDeliveryTransport(endpoint)
        transport._connection = connected
        first = transport.publish("products.xml", b"one", idempotency_key="catalog:1")
        repeated = transport.publish("products.xml", b"one", idempotency_key="catalog:1")
        replaced = transport.publish("products.xml", b"two", idempotency_key="catalog:2")
        self.assertFalse(first["idempotent"])
        self.assertTrue(repeated["idempotent"])
        self.assertFalse(replaced["idempotent"])
        self.assertEqual(connection.files["/exchange/products.xml"], b"two")
        with self.assertRaisesRegex(ValueError, "different published content"):
            transport.publish("products.xml", b"changed", idempotency_key="catalog:2")

    def test_ocstore_nested_order_xml_uses_layout_and_product_model(self):
        layout = {
            "format": "XML",
            "encoding": "UTF-8",
            "root_element": "orders",
            "item_element": "order",
            "fields": [
                {
                    "erp_fieldname": "channel_order_id",
                    "external_column": "order_id",
                    "transform": "none",
                    "required": 1,
                },
                {
                    "erp_fieldname": "channel_status",
                    "external_column": "status",
                    "transform": "none",
                    "required": 1,
                },
                {
                    "erp_fieldname": "customer.phone",
                    "external_column": "telephone",
                    "transform": "none",
                    "required": 1,
                },
            ],
        }
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
        <orders><order><order_id>501</order_id><status>new</status>
        <telephone>0501234567</telephone><currency_code>UAH</currency_code>
        <products><product><product_id>77</product_id><model>SKU-77</model>
        <quantity>2</quantity><price>19.5</price></product></products>
        </order></orders>"""
        parsed = parse_order_file(payload, layout)
        self.assertEqual(parsed[0]["channel_order_id"], "501")
        self.assertEqual(parsed[0]["customer"]["phone"], "0501234567")
        self.assertEqual(parsed[0]["items"][0]["external_id"], "SKU-77")
        self.assertEqual(parsed[0]["items"][0]["variant_sku"], "SKU-77")

    def test_ocstore_order_file_commits_all_orders_before_delete(self):
        events = []

        class Database:
            def set_value(self, *args, **kwargs):
                del args, kwargs

            def commit(self):
                events.append("commit")

            def rollback(self):
                events.append("rollback")

        class Transport:
            def download(self, name):
                del name
                return b"orders"

            def delete(self, name):
                del name
                events.append("delete")
                self.assert_committed = events[-2] == "commit"
                return {"deleted": True}

        settings = types.SimpleNamespace(name="hunter.rv.ua")
        config = types.SimpleNamespace(name="ROW-1")
        transport = Transport()
        rows = [{"channel_order_id": "1"}, {"channel_order_id": "2"}]
        outcomes = [
            {"outcome": "created", "sales_order": "SO-1"},
            {"outcome": "found", "sales_order": "SO-2"},
        ]
        with (
            patch.object(ocstore_service.frappe, "db", Database()),
            patch.object(
                ocstore_service.frappe,
                "utils",
                types.SimpleNamespace(now_datetime=lambda: "now"),
            ),
            patch.object(ocstore_service, "parse_order_file", return_value=rows),
            patch.object(ocstore_service.orders, "intake", side_effect=outcomes),
            patch.object(ocstore_service, "append_sync_log"),
        ):
            result = ocstore_service._process_order_file(
                settings, config, {}, transport, "orders-1.xml"
            )
        self.assertTrue(result["ok"])
        self.assertTrue(transport.assert_committed)
        self.assertEqual(events, ["commit", "delete", "commit"])

    def test_ocstore_partial_order_failure_rolls_back_and_keeps_file(self):
        events = []

        class Database:
            def set_value(self, *args, **kwargs):
                del args, kwargs

            def commit(self):
                events.append("commit")

            def rollback(self):
                events.append("rollback")

        transport = Mock()
        transport.download.return_value = b"orders"
        settings = types.SimpleNamespace(name="hunter.rv.ua")
        config = types.SimpleNamespace(name="ROW-1")
        logger = Mock()
        with (
            patch.object(ocstore_service.frappe, "db", Database()),
            patch.object(
                ocstore_service.frappe,
                "utils",
                types.SimpleNamespace(now_datetime=lambda: "now"),
            ),
            patch.object(
                ocstore_service,
                "parse_order_file",
                return_value=[{"channel_order_id": "1"}, {"channel_order_id": "2"}],
            ),
            patch.object(
                ocstore_service.orders,
                "intake",
                side_effect=[{"outcome": "created"}, ValueError("bad second order")],
            ),
            patch.object(ocstore_service, "append_sync_log", logger),
        ):
            result = ocstore_service._process_order_file(
                settings, config, {}, transport, "orders-2.xml"
            )
        self.assertFalse(result["ok"])
        transport.delete.assert_not_called()
        self.assertEqual(events, ["rollback", "commit"])
        self.assertEqual(logger.call_args.kwargs["status"], "Failed")

    def test_order_intake_uses_channel_and_order_id_without_duplicate_so(self):
        class Database:
            sales_order = None

            def savepoint(self, name):
                self.savepoint_name = name

            def rollback(self, **kwargs):
                self.rollback_args = kwargs

            def get_value(self, doctype, filters, fieldname, **kwargs):
                del fieldname, kwargs
                if doctype == "Sales Order" and filters.get("ua_external_order_key"):
                    return self.sales_order
                return None

        database = Database()
        channel = {
            "doctype": "OcStore Settings",
            "name": "hunter.rv.ua",
            "currency": "UAH",
            "company": "HUNTER",
            "order_status_map": [{"channel_status": "new", "erp_action": "Create Sales Order"}],
        }
        payload = {
            "channel_order_id": "OC-1001",
            "channel_status": "new",
            "customer": {"phone": "050 123 45 67", "name": "Іван"},
            "items": [{"external_id": "42", "sku": "SKU-42", "quantity": 1, "price": 100}],
        }

        def create_sales_order(*args, **kwargs):
            del args, kwargs
            database.sales_order = "SO-0001"
            return types.SimpleNamespace(name="SO-0001")

        with (
            patch.object(orders.frappe, "db", database),
            patch.object(orders, "_resolve_items", return_value=[{"item_code": "ITEM-42"}]),
            patch.object(orders, "_resolve_customer", return_value="CUST-1"),
            patch.object(orders, "_create_sales_order", side_effect=create_sales_order) as creator,
            patch.object(orders, "append_sync_log") as logger,
        ):
            first = orders.intake(channel, payload)
            repeated = orders.intake(channel, payload)

        self.assertEqual(first["outcome"], "created")
        self.assertEqual(repeated["outcome"], "found")
        self.assertEqual(creator.call_count, 1)
        self.assertEqual(logger.call_count, 2)
        self.assertEqual(
            logger.call_args_list[0].kwargs["idempotency_key"],
            logger.call_args_list[1].kwargs["idempotency_key"],
        )

    def test_payment_entry_ledger_key_is_channel_and_order_idempotent(self):
        channel = {
            "doctype": "OcStore Settings",
            "name": "hunter.rv.ua",
            "company": "HUNTER",
            "payment_routes": [
                {
                    "channel_payment_type": "online",
                    "mode_of_payment": "Online",
                    "paid_to_account": "Online Payments - H",
                }
            ],
        }
        order = orders.normalize_order(
            {
                "channel_order_id": "OC-PAID-1",
                "channel_status": "paid",
                "customer": {"phone": "0501234567"},
                "payment": {
                    "type": "online",
                    "amount": 100,
                    "currency": "UAH",
                    "paid": True,
                },
                "items": [{"external_id": "SKU-1", "quantity": 1, "price": 100}],
            }
        )
        invoice = types.SimpleNamespace(name="SINV-1", grand_total=100)
        database = Mock()
        database.get_value.return_value = types.SimpleNamespace(
            company="HUNTER",
            account_currency="UAH",
            is_group=0,
        )
        reservation = types.SimpleNamespace(doc=types.SimpleNamespace(), created=False)
        with (
            patch.object(orders.frappe, "db", database),
            patch.object(orders, "_find_matching_payment_entry", return_value=None),
            patch.object(orders, "reserve_operation", return_value=reservation) as reserve,
            patch.object(
                orders,
                "require_new_or_return_success",
                return_value={"payment_entry": "ACC-PAY-1"},
            ),
        ):
            first = orders._create_payment(
                channel,
                "OcStore Settings:hunter.rv.ua",
                order,
                invoice,
            )
            second = orders._create_payment(
                channel,
                "OcStore Settings:hunter.rv.ua",
                order,
                invoice,
            )
        self.assertEqual((first, second), ("ACC-PAY-1", "ACC-PAY-1"))
        keys = [call.kwargs["idempotency_key"] for call in reserve.call_args_list]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(reserve.call_args.kwargs["integration"], "ecommerce_payment")
        self.assertTrue(reserve.call_args.kwargs["retry_failed"])

    def test_paid_retry_with_existing_so_and_si_reconciles_missing_payment(self):
        channel = {
            "doctype": "OcStore Settings",
            "name": "hunter.rv.ua",
            "company": "HUNTER",
            "currency": "UAH",
        }
        order = orders.normalize_order(
            {
                "channel_order_id": "OC-PAID-RACE-1",
                "channel_status": "paid",
                "customer": {"phone": "0501234567"},
                "payment": {
                    "type": "online",
                    "amount": 100,
                    "currency": "UAH",
                    "paid": True,
                },
                "items": [{"external_id": "SKU-1", "quantity": 1, "price": 100}],
            }
        )
        action = {"erp_action": "Create SO+SI+Payment"}
        sales_order = types.SimpleNamespace(name="SO-1", docstatus=1)
        sales_invoice = types.SimpleNamespace(name="SINV-1", docstatus=1, grand_total=100)

        def get_doc(doctype, name):
            return sales_order if doctype == "Sales Order" else sales_invoice

        with (
            patch.object(
                orders,
                "_existing_documents",
                return_value={"sales_order": "SO-1", "sales_invoice": "SINV-1"},
            ),
            patch.object(orders.frappe, "get_doc", side_effect=get_doc, create=True),
            patch.object(orders, "_create_payment", return_value="ACC-PAY-1") as payment,
            patch.object(orders, "_resolve_items") as resolve_items,
            patch.object(orders, "_resolve_customer") as resolve_customer,
        ):
            result = orders._intake_order(
                channel,
                "OcStore Settings:hunter.rv.ua",
                order,
                "ecom:o:paid-race-1",
                action,
            )

        self.assertEqual(result["outcome"], "reconciled")
        self.assertEqual(result["payment_entry"], "ACC-PAY-1")
        payment.assert_called_once_with(
            channel,
            "OcStore Settings:hunter.rv.ua",
            order,
            sales_invoice,
        )
        resolve_items.assert_not_called()
        resolve_customer.assert_not_called()

    def test_paid_retry_with_only_so_builds_invoice_then_payment(self):
        channel = {
            "doctype": "OcStore Settings",
            "name": "hunter.rv.ua",
            "company": "HUNTER",
            "currency": "UAH",
        }
        order = orders.normalize_order(
            {
                "channel_order_id": "OC-PAID-RACE-2",
                "channel_status": "paid",
                "customer": {"phone": "0501234567"},
                "payment": {
                    "type": "online",
                    "amount": 100,
                    "currency": "UAH",
                    "paid": True,
                },
                "items": [{"external_id": "SKU-1", "quantity": 1, "price": 100}],
            }
        )
        sales_order = types.SimpleNamespace(name="SO-2", docstatus=1)
        sales_invoice = types.SimpleNamespace(name="SINV-2", docstatus=1, grand_total=100)
        with (
            patch.object(orders, "_existing_documents", return_value={"sales_order": "SO-2"}),
            patch.object(orders.frappe, "get_doc", return_value=sales_order, create=True),
            patch.object(
                orders,
                "_create_invoice_from_order",
                return_value=sales_invoice,
            ) as create_invoice,
            patch.object(orders, "_create_payment", return_value="ACC-PAY-2") as payment,
        ):
            result = orders._intake_order(
                channel,
                "OcStore Settings:hunter.rv.ua",
                order,
                "ecom:o:paid-race-2",
                {"erp_action": "Create SO+SI+Payment"},
            )

        self.assertEqual(result["outcome"], "reconciled")
        create_invoice.assert_called_once_with(
            sales_order,
            "ecom:o:paid-race-2",
            "OcStore Settings:hunter.rv.ua",
            order,
        )
        payment.assert_called_once()

    def test_existing_matching_payment_is_reused_from_invoice_references(self):
        reference = types.SimpleNamespace(parent="ACC-PAY-3", allocated_amount=100)
        payment = types.SimpleNamespace(
            name="ACC-PAY-3",
            docstatus=1,
            mode_of_payment="Online",
            paid_to="Online Payments - H",
        )
        database = Mock()
        database.get_value.return_value = payment
        with (
            patch.object(orders.frappe, "db", database),
            patch.object(orders.frappe, "get_all", return_value=[reference], create=True),
        ):
            result = orders._find_matching_payment_entry(
                "SINV-3",
                amount=100,
                mode_of_payment="Online",
                paid_to_account="Online Payments - H",
            )
        self.assertEqual(result, "ACC-PAY-3")

    def test_success_log_is_a_second_order_idempotency_guard(self):
        database = Mock()
        database.get_value.side_effect = lambda doctype, filters, fieldname, **kwargs: (
            "ECOM-SYNC-1" if doctype == "Ecommerce Sync Log" else None
        )
        with patch.object(orders.frappe, "db", database):
            existing = orders._existing_documents("ecom:o:key")
        self.assertEqual(existing, {"sync_log": "ECOM-SYNC-1"})

    def test_order_normalization_handles_phone_and_boolean_strings(self):
        normalized = orders.normalize_order(
            {
                "channel_order_id": "1",
                "channel_status": "new",
                "paid": "0",
                "customer": {"phone": "(050) 123-45-67"},
                "items": [{"external_id": "1", "quantity": 1, "price": 1}],
            }
        )
        self.assertEqual(normalized.customer.phone, "+380501234567")
        self.assertFalse(normalized.payment.paid)

    def test_mapping_patch_is_idempotent_and_adds_one_composite_constraint(self):
        row = types.SimpleNamespace(
            name="MAP-1",
            channel="ocstore-old",
            item="ITEM-1",
            external_id="",
            external_sku="SKU-1",
            variant_sku="",
            sync_status="",
            mapping_key="",
            external_mapping_key="",
        )

        class Database:
            indexed = False
            add_unique_calls = 0

            def exists(self, *args):
                return True

            def get_table_columns(self, doctype):
                del doctype
                return {
                    "channel",
                    "item",
                    "external_id",
                    "external_sku",
                    "variant_sku",
                    "sync_status",
                    "mapping_key",
                    "external_mapping_key",
                }

            def set_value(self, doctype, name, values, **kwargs):
                del doctype, name, kwargs
                for key, value in values.items():
                    setattr(row, key, value)

            def has_index(self, table, index):
                del table, index
                return self.indexed

            def add_unique(self, doctype, fields, constraint_name):
                del doctype, fields, constraint_name
                self.indexed = True
                self.add_unique_calls += 1

        database = Database()
        with (
            patch.object(backfill_ecommerce_item_mapping.frappe, "db", database),
            patch.object(
                backfill_ecommerce_item_mapping.frappe,
                "get_all",
                return_value=[row],
                create=True,
            ),
        ):
            backfill_ecommerce_item_mapping.execute()
            first_values = (row.external_id, row.variant_sku, row.mapping_key, row.external_mapping_key)
            backfill_ecommerce_item_mapping.execute()

        self.assertEqual(first_values, (row.external_id, row.variant_sku, row.mapping_key, row.external_mapping_key))
        self.assertEqual(database.add_unique_calls, 1)

    def test_mapping_module_move_patch_is_idempotent(self):
        class Database:
            module = "Ukrainian Integrations"
            writes = 0

            def exists(self, *args):
                return True

            def get_value(self, *args):
                return self.module

            def set_value(self, doctype, name, fieldname, value, **kwargs):
                del doctype, name, fieldname, kwargs
                self.module = value
                self.writes += 1

        database = Database()
        with (
            patch.object(move_ecommerce_item_mapping_to_module.frappe, "db", database),
            patch.object(
                move_ecommerce_item_mapping_to_module.frappe,
                "clear_cache",
                create=True,
            ),
        ):
            move_ecommerce_item_mapping_to_module.execute()
            move_ecommerce_item_mapping_to_module.execute()

        self.assertEqual(database.module, "Ecommerce")
        self.assertEqual(database.writes, 1)

    def test_ecommerce_module_registration_patch_is_idempotent(self):
        state = {"owner": None, "doctype_exists": True, "module": "Ecommerce"}

        class Database:
            def get_value(self, doctype, name, fieldname):
                if doctype == "Module Def":
                    return state["owner"]
                if doctype == "DocType":
                    return state["module"]
                return None

            def exists(self, doctype, name):
                return doctype == "DocType" and name == "Ecommerce Item Mapping"

            def set_value(self, doctype, name, fieldname, value, **kwargs):
                del name, fieldname, kwargs
                if doctype == "DocType":
                    state["module"] = value

        class ModuleDef:
            module_name = None
            app_name = None

            def insert(self, **kwargs):
                del kwargs
                state["owner"] = self.app_name

        cache = types.SimpleNamespace(delete_value=Mock())
        client_cache = types.SimpleNamespace(delete_value=Mock())
        with (
            patch.object(register_ecommerce_module_and_sync_mapping.frappe, "db", Database()),
            patch.object(
                register_ecommerce_module_and_sync_mapping.frappe,
                "new_doc",
                return_value=ModuleDef(),
                create=True,
            ) as new_doc,
            patch.object(register_ecommerce_module_and_sync_mapping.frappe, "cache", cache, create=True),
            patch.object(
                register_ecommerce_module_and_sync_mapping.frappe,
                "client_cache",
                client_cache,
                create=True,
            ),
            patch.object(
                register_ecommerce_module_and_sync_mapping.frappe,
                "setup_module_map",
                create=True,
            ) as setup_module_map,
            patch.object(
                register_ecommerce_module_and_sync_mapping.frappe,
                "reload_doc",
                create=True,
            ) as reload_doc,
            patch.object(
                register_ecommerce_module_and_sync_mapping.frappe,
                "clear_cache",
                create=True,
            ),
        ):
            register_ecommerce_module_and_sync_mapping.execute()
            register_ecommerce_module_and_sync_mapping.execute()

        self.assertEqual(state["owner"], "ukrainian_integrations")
        new_doc.assert_called_once_with("Module Def")
        self.assertEqual(setup_module_map.call_count, 2)
        self.assertEqual(reload_doc.call_count, 2)
        reload_doc.assert_called_with("ecommerce", "doctype", "ecommerce_item_mapping", force=True)

    def test_ecommerce_module_registration_rejects_foreign_owner(self):
        database = Mock()
        database.get_value.return_value = "other_app"
        with (
            patch.object(register_ecommerce_module_and_sync_mapping.frappe, "db", database),
            self.assertRaisesRegex(RuntimeError, "already owned by app other_app"),
        ):
            register_ecommerce_module_and_sync_mapping.execute()

    def test_legacy_channel_link_conversion_is_idempotent_and_preserves_values(self):
        state = types.SimpleNamespace(fieldtype="Link", options="Ecommerce Channel")

        class Database:
            writes = 0

            def get_value(self, doctype, filters, fieldnames, **kwargs):
                del kwargs
                if doctype != "Custom Field":
                    return None
                if isinstance(filters, dict):
                    return "Sales Order-ua_ecommerce_channel" if filters.get("dt") == "Sales Order" else None
                if fieldnames == ["fieldtype", "options"]:
                    return state
                return None

            def set_value(self, doctype, name, values, **kwargs):
                del doctype, name, kwargs
                state.fieldtype = values["fieldtype"]
                state.options = values["options"]
                self.writes += 1

        database = Database()
        with (
            patch.object(convert_ecommerce_channel_custom_field_to_data.frappe, "db", database),
            patch.object(
                convert_ecommerce_channel_custom_field_to_data.frappe,
                "clear_cache",
                create=True,
            ),
        ):
            convert_ecommerce_channel_custom_field_to_data.execute()
            convert_ecommerce_channel_custom_field_to_data.execute()

        self.assertEqual((state.fieldtype, state.options), ("Data", None))
        self.assertEqual(database.writes, 1)


if __name__ == "__main__":
    unittest.main()
