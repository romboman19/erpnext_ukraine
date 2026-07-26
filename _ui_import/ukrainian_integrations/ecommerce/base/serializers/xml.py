from __future__ import annotations

import re
from typing import Any

# ElementTree only constructs trusted outbound XML; defusedxml parses inbound payloads.
from xml.etree import ElementTree as ET  # nosec B405

from defusedxml.ElementTree import fromstring as safe_xml_fromstring

from .common import (
    bounded_records,
    encoding,
    ensure_bounded,
    external_row,
    internal_row,
    value,
)

_XML_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def validate_xml_name(name: str, label: str) -> str:
    parsed = str(name or "").strip()
    if not _XML_NAME.fullmatch(parsed):
        raise ValueError(f"{label} must be a valid XML element name")
    return parsed


class XMLSerializer:
    format = "XML"

    def serialize(self, records: list[dict], layout: Any) -> bytes:
        root_name = validate_xml_name(value(layout, "root_element", "records"), "Root element")
        item_name = validate_xml_name(value(layout, "item_element", "item"), "Item element")
        root = ET.Element(root_name)
        for record in bounded_records(records):
            item = ET.SubElement(root, item_name)
            for field_name, field_value in external_row(record, layout).items():
                child = ET.SubElement(item, validate_xml_name(field_name, "External column"))
                child.text = field_value
        if hasattr(ET, "indent"):
            ET.indent(root, space="  ")
        codec = encoding(layout)
        xml_encoding = "utf-8" if codec == "utf-8-sig" else codec
        payload = ET.tostring(root, encoding=xml_encoding, xml_declaration=True, short_empty_elements=True)
        if codec == "utf-8-sig" and not payload.startswith(b"\xef\xbb\xbf"):
            payload = b"\xef\xbb\xbf" + payload
        return ensure_bounded(payload)

    def deserialize(self, payload: bytes | str, layout: Any) -> list[dict]:
        raw = payload.encode(encoding(layout)) if isinstance(payload, str) else bytes(payload)
        ensure_bounded(raw)
        normalized = raw.upper()
        if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
            raise ValueError("DTD and XML entities are not allowed in ecommerce files")
        try:
            root = safe_xml_fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError("Ecommerce XML file is invalid") from exc
        expected_root = validate_xml_name(value(layout, "root_element", "records"), "Root element")
        item_name = validate_xml_name(value(layout, "item_element", "item"), "Item element")
        if _local_name(root.tag) != expected_root:
            raise ValueError(f"Unexpected XML root element: {_local_name(root.tag)}")
        rows = []
        for index, item in enumerate(root):
            if _local_name(item.tag) != item_name:
                continue
            if index >= 100_000:
                raise ValueError("XML file contains too many records")
            external = {_local_name(child.tag): (child.text or "") for child in item}
            rows.append(internal_row(external, layout))
        return rows


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]
