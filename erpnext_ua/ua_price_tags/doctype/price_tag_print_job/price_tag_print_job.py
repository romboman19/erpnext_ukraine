from __future__ import annotations

import hashlib
import json

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from erpnext_ua.ua_price_tags.domain import MAX_COPIES_PER_ITEM, MAX_LABELS_PER_JOB


ALLOWED_TRANSITIONS = {
	"Draft": {"Draft", "Ready"},
	"Ready": {"Ready", "Printed", "Error"},
	"Error": {"Error", "Ready"},
	"Printed": {"Printed"},
}
SNAPSHOT_HEADER_FIELDS = (
	"company",
	"warehouse",
	"price_list",
	"promotional_price_list",
	"currency",
	"template_type",
	"label_size",
	"print_method",
	"print_format",
	"reason",
	"source_doctype",
	"source_name",
	"source_label",
	"reprint_of",
)
SNAPSHOT_ITEM_FIELDS = (
	"item_code",
	"item_name",
	"barcode",
	"barcode_svg",
	"uom",
	"variant_text",
	"copies",
	"stock_qty",
	"regular_price",
	"selling_price",
	"old_price",
	"currency",
	"is_promotional",
	"promotion_from",
	"promotion_upto",
	"promotion_text",
	"item_price",
	"promotional_item_price",
	"pricing_rule",
	"source_row",
	"source_warehouse",
)


class PriceTagPrintJob(Document):
	def before_validate(self):
		self.created_by_user = self.created_by_user or frappe.session.user
		self.status = self.status or "Draft"
		self.print_method = self.print_method or "PDF"
		self.total_labels = sum(max(0, int(row.copies or 0)) for row in self.items or [])

	def validate(self):
		self._validate_rows()
		self._validate_transition()
		current_hash = self._make_snapshot_hash()
		before = self.get_doc_before_save()
		if before and before.status != "Draft":
			expected_hash = before.snapshot_hash or self._make_snapshot_hash(before)
			if current_hash != expected_hash:
				frappe.throw("Зафіксований пакет друку не можна змінювати; створіть новий пакет")
			self.snapshot_hash = expected_hash
		elif self.status == "Draft":
			self.snapshot_hash = None
		else:
			self.snapshot_hash = current_hash

		if self.status == "Ready" and not self.ready_at:
			self.ready_at = now_datetime()
		if self.status == "Printed":
			self.printed_by = self.printed_by or frappe.session.user
			self.printed_at = self.printed_at or now_datetime()

	def _validate_rows(self):
		if not self.items:
			frappe.throw("Додайте хоча б один товар до пакета друку")
		if self.total_labels > MAX_LABELS_PER_JOB:
			frappe.throw(f"Один пакет може містити не більше {MAX_LABELS_PER_JOB} цінників")
		for row in self.items:
			if int(row.copies or 0) < 1:
				frappe.throw(f"Рядок {row.idx}: кількість копій має бути більшою за нуль")
			if int(row.copies or 0) > MAX_COPIES_PER_ITEM:
				frappe.throw(f"Рядок {row.idx}: дозволено не більше {MAX_COPIES_PER_ITEM} копій")
			if self.template_type != "Packaging" and (
				row.selling_price is None or float(row.selling_price) < 0
			):
				frappe.throw(f"Рядок {row.idx}: ціна не визначена")
			if bool(row.is_promotional) != (self.template_type == "Promotional"):
				frappe.throw("Один пакет не може змішувати різні типи етикеток")

	def _validate_transition(self):
		before = self.get_doc_before_save()
		if not before:
			return
		allowed = ALLOWED_TRANSITIONS.get(before.status or "Draft", set())
		if self.status not in allowed:
			frappe.throw(f"Недопустима зміна статусу: {before.status} → {self.status}")

	def _make_snapshot_hash(self, document=None):
		document = document or self
		payload = {field: document.get(field) for field in SNAPSHOT_HEADER_FIELDS}
		payload["items"] = [
			{field: row.get(field) for field in SNAPSHOT_ITEM_FIELDS} for row in document.items or []
		]
		encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
		return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

	@frappe.whitelist()
	def mark_ready(self):
		self.check_permission("write")
		self.status = "Ready"
		self.save()
		return self.name

	@frappe.whitelist()
	def mark_printed(self):
		self.check_permission("write")
		if self.status != "Ready":
			frappe.throw("Позначити надрукованим можна лише готовий пакет")
		self.status = "Printed"
		self.save()
		return self.name

	def on_trash(self):
		if self.status != "Draft":
			frappe.throw("Готовий або надрукований пакет є частиною журналу і не може бути видалений")
