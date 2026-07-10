import re

import frappe
from frappe.model.document import Document

RNOKPP_WEIGHTS = (-1, 5, 7, 9, 4, 6, 10, 5, 7)

GROUP_RATE_MODES = {
	"1": {"Фіксована ставка"},
	"2": {"Фіксована ставка"},
	"3": {"5% без ПДВ", "3% з ПДВ"},
}


def validate_rnokpp(tax_id: str) -> bool:
	"""Перевірка контрольної суми РНОКПП (10 цифр)."""
	if not re.fullmatch(r"\d{10}", tax_id):
		return False
	digits = [int(d) for d in tax_id]
	control = sum(d * w for d, w in zip(digits[:9], RNOKPP_WEIGHTS)) % 11 % 10
	return control == digits[9]


class FOPProfile(Document):
	def validate(self):
		self.validate_tax_id()
		self.validate_tax_mode()
		self.validate_iban()

	def validate_tax_id(self):
		self.tax_id = (self.tax_id or "").strip()
		if not validate_rnokpp(self.tax_id):
			frappe.throw(
				"РНОКПП має складатися з 10 цифр і мати коректну контрольну суму. "
				f"Введено: {self.tax_id!r}"
			)

	def validate_tax_mode(self):
		allowed = GROUP_RATE_MODES.get(self.single_tax_group, set())
		if self.tax_rate_mode not in allowed:
			frappe.throw(
				f"Режим ставки «{self.tax_rate_mode}» недоступний для групи {self.single_tax_group}. "
				f"Доступні: {', '.join(sorted(allowed))}"
			)
		self.vat_payer = 1 if self.tax_rate_mode == "3% з ПДВ" else 0
		if not self.vat_payer:
			self.vat_number = None

	def validate_iban(self):
		if not self.iban:
			return
		self.iban = self.iban.replace(" ", "").upper()
		if not re.fullmatch(r"UA\d{27}", self.iban):
			frappe.throw("Український IBAN має формат UA + 27 цифр (разом 29 символів)")

	@frappe.whitelist()
	def get_current_tax_parameters(self):
		"""Параметри податків для групи цього ФОП на поточний рік."""
		year = frappe.utils.getdate().year
		name = frappe.db.exists(
			"UA Tax Parameters", {"year": year, "single_tax_group": self.single_tax_group}
		)
		return frappe.get_doc("UA Tax Parameters", name).as_dict() if name else None
