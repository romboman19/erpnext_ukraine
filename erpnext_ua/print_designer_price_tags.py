"""Native Print Designer layouts for 40x25 mm price-tag packets."""

from __future__ import annotations

from erpnext_ua.print_designer_layout import (
	MM_TO_PX,
	layout_row,
	page_settings,
	print_format_document,
	static_jinja_field,
	table_column,
	table_element,
)


STANDARD_FORMAT_NAME = "Цінник звичайний 40x25 (Print Designer)"
PROMOTIONAL_FORMAT_NAME = "Цінник акційний 40x25 (Print Designer)"
PACKAGING_FORMAT_NAME = "Етикетка на упаковку 40x25 (Print Designer)"
PRICE_TAG_FORMAT_FIELDS = {
	"standard_print_format": ("Цінник звичайний 40x25", STANDARD_FORMAT_NAME),
	"promotional_print_format": ("Цінник акційний 40x25", PROMOTIONAL_FORMAT_NAME),
	"packaging_print_format": ("Етикетка на упаковку 40x25", PACKAGING_FORMAT_NAME),
}


def build_price_tag_formats(base_settings: dict) -> list[dict]:
	return [
		_build_format(base_settings, STANDARD_FORMAT_NAME, "standard", _standard_columns()),
		_build_format(base_settings, PROMOTIONAL_FORMAT_NAME, "promotional", _promotional_columns()),
		_build_format(base_settings, PACKAGING_FORMAT_NAME, "packaging", _packaging_columns()),
	]


def _build_format(base_settings, name, variant, columns):
	page_width = 40 * MM_TO_PX
	page_height = 25 * MM_TO_PX
	settings = page_settings(
		base_settings,
		width=page_width,
		height=page_height,
		page_size="CUSTOM",
		user_jinja=_EXPAND_COPIES_JINJA,
	)
	table = table_element(
		f"price-tag-{variant}",
		table_fieldname="items",
		table_label="Цінники",
		table_options="Price Tag Print Job Item",
		columns=columns,
		x=0,
		y=0,
		width=page_width,
		height=page_height,
		classes=("ua-price-tag-pd", f"ua-price-tag-{variant}"),
		style={
			"backgroundColor": "#ffffff",
			"border": "none",
			"borderWidth": "0px",
			"fontFamily": "DejaVu Sans",
			"margin": "0px",
			"paddingBottom": "0px",
			"paddingLeft": "0px",
			"paddingRight": "0px",
			"paddingTop": "0px",
		},
		header_style={
			"border": "none",
			"borderWidth": "0px",
			"display": "none",
			"fontSize": "0px",
			"paddingBottom": "0px",
			"paddingLeft": "0px",
			"paddingRight": "0px",
			"paddingTop": "0px",
		},
	)
	layout_rows = [
		layout_row(
			f"price-tag-row-{variant}",
			[table],
			width=page_width,
			height=page_height,
			height_type="auto",
		),
	]
	return print_format_document(
		name=name,
		doc_type="Price Tag Print Job",
		module="UA Price Tags",
		settings=settings,
		elements=[table],
		layout_rows=layout_rows,
		css=_PRICE_TAG_CSS,
	)


def _standard_columns():
	return [
		table_column(0, "Назва", 45, [static_jinja_field("{{ row.item_name }}{% if row.variant_text %} {{ row.variant_text }}{% endif %}")]),
		table_column(1, "Код", 15, [static_jinja_field("{{ row.item_code }}")]),
		table_column(2, "Ціна", 20, [static_jinja_field('{{ ("%.2f"|format(row.selling_price|float)).replace(".", ",") }}')]),
		table_column(3, "Штрихкод", 20, [_barcode_field()]),
	]


def _promotional_columns():
	return [
		table_column(0, "Назва", 40, [static_jinja_field("{{ row.item_name }}{% if row.variant_text %} {{ row.variant_text }}{% endif %}")]),
		table_column(1, "Код", 15, [static_jinja_field("{{ row.item_code }}")]),
		table_column(2, "Стара ціна", 15, [static_jinja_field('{{ ("%.2f"|format(row.old_price|float)).replace(".", ",") }}')]),
		table_column(3, "Нова ціна", 15, [static_jinja_field('{{ ("%.2f"|format(row.selling_price|float)).replace(".", ",") }}')]),
		table_column(4, "Штрихкод", 15, [_barcode_field()]),
	]


def _packaging_columns():
	return [
		table_column(0, "Назва", 50, [static_jinja_field("{{ row.item_name }}{% if row.variant_text %} {{ row.variant_text }}{% endif %}")]),
		table_column(1, "Код", 20, [static_jinja_field("{{ row.item_code }}")]),
		table_column(2, "Штрихкод", 30, [_barcode_field()]),
	]


def _barcode_field():
	return static_jinja_field(
		'{% if row.barcode_svg %}<img class="ua-label-barcode" src="{{ row.barcode_svg }}">{% else %}<span class="ua-label-barcode-text">{{ row.barcode or row.item_code }}</span>{% endif %}'
	)


_EXPAND_COPIES_JINJA = """
{% set original_items = doc.get("items") or [] %}
{% set expanded_items = [] %}
{% for row in original_items %}
  {% for copy in range(row.copies|int) %}
    {% set ignored = expanded_items.append(row) %}
  {% endfor %}
{% endfor %}
{% set ignored = doc.set("items", expanded_items) %}
{% set send_to_jinja = {} %}
""".strip()


_PRICE_TAG_CSS = """
@page { size: 40mm 25mm; margin: 0; }
.print-format, #__print_designer { margin: 0 !important; padding: 0 !important; }
.ua-price-tag-pd { position: static !important; width: 40mm !important; max-width: 40mm !important; margin: 0 !important; border: 0 !important; border-collapse: collapse !important; table-layout: fixed !important; }
.ua-price-tag-pd thead { display: none !important; }
.ua-price-tag-pd tbody { display: block !important; width: 40mm !important; }
.ua-price-tag-pd tbody tr { position: relative !important; display: block !important; box-sizing: border-box !important; width: 40mm !important; height: 25mm !important; overflow: hidden !important; color: #000 !important; background: #fff !important; break-after: page !important; page-break-after: always !important; font-family: "DejaVu Sans", sans-serif !important; }
.ua-price-tag-pd tbody tr:last-child { break-after: auto !important; page-break-after: auto !important; }
.ua-price-tag-pd tbody td { position: absolute !important; display: block !important; box-sizing: border-box !important; margin: 0 !important; padding: 0 !important; border: 0 !important; overflow: hidden !important; line-height: 1.05 !important; }
.ua-price-tag-pd .dynamic-span { display: block !important; }
.ua-label-barcode { display: block !important; width: 100% !important; height: 100% !important; object-fit: fill !important; }
.ua-label-barcode-text { display: block !important; width: 100% !important; font-size: 5pt !important; text-align: center !important; white-space: nowrap !important; }
.ua-price-tag-standard tbody tr { padding: .8mm 1mm .5mm !important; }
.ua-price-tag-standard tbody td:nth-child(1) { top: .8mm !important; left: 1mm !important; width: 38mm !important; height: 4.6mm !important; font-size: 7.5pt !important; white-space: nowrap !important; text-overflow: ellipsis !important; }
.ua-price-tag-standard tbody td:nth-child(2) { top: 10mm !important; left: 1mm !important; width: 14mm !important; height: 3mm !important; font-size: 5.2pt !important; white-space: nowrap !important; }
.ua-price-tag-standard tbody td:nth-child(3) { top: 5.7mm !important; right: 1mm !important; width: 23mm !important; height: 8mm !important; font-size: 20pt !important; font-weight: 800 !important; letter-spacing: -.45mm !important; line-height: .85 !important; text-align: right !important; white-space: nowrap !important; }
.ua-price-tag-standard tbody td:nth-child(4) { top: 15.8mm !important; right: 1mm !important; width: 27mm !important; height: 8.2mm !important; }
.ua-price-tag-promotional tbody tr { padding: .8mm 1mm .5mm !important; }
.ua-price-tag-promotional tbody td:nth-child(1) { top: .8mm !important; left: 1mm !important; width: 38mm !important; height: 4.4mm !important; font-size: 7.4pt !important; white-space: nowrap !important; text-overflow: ellipsis !important; }
.ua-price-tag-promotional tbody td:nth-child(2) { top: 10mm !important; left: 1mm !important; width: 13mm !important; height: 3mm !important; font-size: 5.2pt !important; white-space: nowrap !important; }
.ua-price-tag-promotional tbody td:nth-child(3) { top: 5.4mm !important; right: 1mm !important; width: 22mm !important; height: 3.2mm !important; font-size: 8pt !important; text-align: right !important; text-decoration: line-through !important; white-space: nowrap !important; }
.ua-price-tag-promotional tbody td:nth-child(4) { top: 8mm !important; right: 1mm !important; width: 23mm !important; height: 6.5mm !important; font-size: 19pt !important; font-weight: 800 !important; letter-spacing: -.4mm !important; line-height: .85 !important; text-align: right !important; white-space: nowrap !important; }
.ua-price-tag-promotional tbody td:nth-child(5) { top: 15.8mm !important; right: 1mm !important; width: 27mm !important; height: 8.2mm !important; }
.ua-price-tag-packaging tbody tr { padding: 1mm 1.2mm .5mm !important; }
.ua-price-tag-packaging tbody td:nth-child(1) { top: 1mm !important; left: 1.2mm !important; width: 37.6mm !important; height: 6mm !important; font-size: 7.5pt !important; text-align: center !important; white-space: nowrap !important; text-overflow: ellipsis !important; }
.ua-price-tag-packaging tbody td:nth-child(2) { top: 7mm !important; left: 1.2mm !important; width: 20mm !important; height: 3.4mm !important; font-size: 5.4pt !important; white-space: nowrap !important; }
.ua-price-tag-packaging tbody td:nth-child(3) { top: 11.5mm !important; left: 5.5mm !important; width: 29mm !important; height: 12.5mm !important; }
""".strip()
