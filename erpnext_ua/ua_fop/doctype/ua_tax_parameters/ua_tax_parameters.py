from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from erpnext_ua.ua_fop.tax_rules import missing_parameter_fields


def _is_official_source(url: str) -> bool:
	parsed = urlparse(url)
	hostname = (parsed.hostname or "").lower()
	return parsed.scheme == "https" and (hostname == "gov.ua" or hostname.endswith(".gov.ua"))


class UATaxParameters(Document):
	def validate(self):
		self._validate_identity()
		self._validate_required_values()
		self._validate_rate_ceiling()
		self._validate_sources()

	def _validate_identity(self):
		if self.year and (self.year < 2020 or self.year > 2100):
			frappe.throw(_("Некоректний рік"))
		if not self.is_new() and (
			self.has_value_changed("year") or self.has_value_changed("single_tax_group")
		):
			frappe.throw(_("Рік і групу чинного набору параметрів змінювати не можна"))
		exists = frappe.db.exists(
			"UA Tax Parameters",
			{"year": self.year, "single_tax_group": self.single_tax_group, "name": ("!=", self.name)},
		)
		if exists:
			frappe.throw(
				_("Параметри для {0} року, група {1} вже існують: {2}").format(
					self.year, self.single_tax_group, exists
				)
			)

	def _validate_required_values(self):
		missing = missing_parameter_fields(self.single_tax_group, self.as_dict())
		if missing:
			frappe.throw(_("Неповний набір податкових параметрів: {0}").format(", ".join(missing)))

	def _validate_rate_ceiling(self):
		if self.single_tax_group == "1":
			ceiling = flt(self.subsistence_minimum) * 0.10
		elif self.single_tax_group == "2":
			ceiling = flt(self.minimum_wage) * 0.20
		else:
			return
		if flt(self.single_tax_monthly) > ceiling + 0.005:
			frappe.throw(
				_("Місячна ставка ЄП {0} перевищує законодавчий максимум {1}").format(
					frappe.utils.fmt_money(self.single_tax_monthly, currency="UAH"),
					frappe.utils.fmt_money(ceiling, currency="UAH"),
				)
			)

	def _validate_sources(self):
		sources = [line.strip() for line in (self.official_sources or "").splitlines() if line.strip()]
		if not sources:
			frappe.throw(_("Додайте хоча б одне офіційне нормативне джерело"))
		invalid = [source for source in sources if not _is_official_source(source)]
		if invalid:
			frappe.throw(
				_("Дозволені лише HTTPS-посилання на офіційні домени gov.ua: {0}").format(
					", ".join(invalid)
				)
			)
		self.official_sources = "\n".join(sources)
