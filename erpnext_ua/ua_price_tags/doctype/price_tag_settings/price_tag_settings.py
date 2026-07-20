import frappe
from frappe.model.document import Document


class PriceTagSettings(Document):
	def before_validate(self):
		self.default_price_list = self.default_price_list or frappe.get_single_value(
			"Selling Settings", "selling_price_list"
		)
		if self.default_label_size == "58×40 mm":
			self.default_label_size = "40×25 mm"
		if self.standard_print_format == "Цінник стандартний 58x40":
			self.standard_print_format = "Цінник звичайний 40x25"
		if self.promotional_print_format == "Цінник акційний 58x40":
			self.promotional_print_format = "Цінник акційний 40x25"
		self.default_label_size = self.default_label_size or "40×25 mm"
		self.default_copies = max(1, int(self.default_copies or 1))
		self.standard_print_format = self.standard_print_format or "Цінник звичайний 40x25"
		self.promotional_print_format = self.promotional_print_format or "Цінник акційний 40x25"
		self.packaging_print_format = self.packaging_print_format or "Етикетка на упаковку 40x25"

	def validate(self):
		if self.promotional_price_list and self.promotional_price_list == self.default_price_list:
			frappe.throw("Акційний і роздрібний прайс-листи мають відрізнятися")
		for fieldname in ("default_price_list", "promotional_price_list"):
			price_list = self.get(fieldname)
			if price_list and not frappe.db.get_value("Price List", price_list, "selling"):
				frappe.throw(f"Прайс-лист {price_list} не є прайс-листом продажу")
		if self.promotional_price_list:
			regular_currency = frappe.db.get_value("Price List", self.default_price_list, "currency")
			promotional_currency = frappe.db.get_value("Price List", self.promotional_price_list, "currency")
			if regular_currency != promotional_currency:
				frappe.throw("Роздрібний та акційний прайс-листи повинні мати однакову валюту")
