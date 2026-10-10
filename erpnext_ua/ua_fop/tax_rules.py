"""Pure Ukrainian sole-proprietor tax calendar rules.

The module intentionally has no Frappe imports. Date rules are therefore easy
to review against official guidance and test without a site or database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

QUARTERS = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
MONTH_NAMES = (
	"січень",
	"лютий",
	"березень",
	"квітень",
	"травень",
	"червень",
	"липень",
	"серпень",
	"вересень",
	"жовтень",
	"листопад",
	"грудень",
)

COMMON_PARAMETER_FIELDS = (
	"minimum_wage",
	"income_limit",
	"esv_monthly",
	"official_sources",
	"verified_on",
)

# Режими ЄСВ «за себе» для одного ФОП. Звільнення — ст. 4 ч. 4 та ч. 6 Закону
# 2464-VI; ставка 22% мінімальної зарплати живе в UA Tax Parameters, не тут.
ESV_MODE_MINIMUM = "Мінімальний внесок"
ESV_MODE_EXEMPT = "Звільнений"
ESV_MODE_ABOVE_MINIMUM = "Більше мінімуму"
ESV_MODES = (ESV_MODE_MINIMUM, ESV_MODE_EXEMPT, ESV_MODE_ABOVE_MINIMUM)

ESV_EXEMPTION_REASONS = (
	"Пенсіонер за віком",
	"Особа з інвалідністю, отримує пенсію або соцдопомогу",
	"ЄСВ сплачує роботодавець за основним місцем роботи",
	"Інше (вказати)",
)
ESV_EXEMPTION_OTHER = ESV_EXEMPTION_REASONS[-1]

# Збережено дослівно: цей текст уже стоїть у згенерованих рядках ЄСВ, і зміна
# формулювання переписала б примітки в наявних записах без зміни змісту.
ESV_MINIMUM_QUARTER_NOTE = (
	"Мінімальний ЄСВ «за себе» за 3 місяці; строк «до 20 числа» означає 19-те. "
	"Перевірте індивідуальні пільги та звільнення."
)
GROUP_PARAMETER_FIELDS = {
	"1": ("subsistence_minimum", "single_tax_monthly", "military_levy_monthly"),
	"2": ("single_tax_monthly", "military_levy_monthly"),
	"3": ("single_tax_percent_no_vat", "single_tax_percent_vat", "military_levy_percent"),
}


def is_official_source(url: str) -> bool:
	parsed = urlparse(url)
	hostname = (parsed.hostname or "").lower()
	return parsed.scheme == "https" and (hostname == "gov.ua" or hostname.endswith(".gov.ua"))


def official_source_urls(value: str | None) -> tuple[str, ...]:
	return tuple(line.strip() for line in (value or "").splitlines() if line.strip())


@dataclass(frozen=True, slots=True)
class TaxAmounts:
	single_tax_monthly: float | None = None
	military_levy_monthly: float | None = None
	esv_monthly: float | None = None
	single_tax_percent_no_vat: float | None = None
	single_tax_percent_vat: float | None = None
	military_levy_percent: float | None = None


@dataclass(frozen=True, slots=True)
class ESVContext:
	"""Обставини ЄСВ «за себе» одного ФОП, без залежності від Document.

	`exemption_from` / `exemption_to` трактуються з точністю до місяця: місяць
	вважається звільненим, якщо він потрапляє в інтервал. Який саме місяць є
	першим звільненим (наприклад наступний після набуття статусу пенсіонера) —
	вирішує власник або Фінансист і вводить у профіль; код цього не домислює.
	"""

	mode: str = ESV_MODE_MINIMUM
	monthly_override: float | None = None
	exemption_from: date | None = None
	exemption_to: date | None = None
	exemption_reason: str = ""

	def monthly_amount(self, parameter_monthly: float | None) -> float | None:
		"""Підтверджена сума ФОП має приоритет над параметром року."""
		return self.monthly_override if self.monthly_override is not None else parameter_monthly

	def is_month_exempt(self, year: int, month: int) -> bool:
		if self.mode != ESV_MODE_EXEMPT:
			return False
		if self.exemption_from is None:
			# Без дати початку звільнення немає чого відраховувати; нараховуємо.
			return False
		index = _month_index(year, month)
		if index < _month_index(self.exemption_from.year, self.exemption_from.month):
			return False
		if self.exemption_to is not None and index > _month_index(
			self.exemption_to.year, self.exemption_to.month
		):
			return False
		return True


def _month_index(year: int, month: int) -> int:
	return year * 12 + month


@dataclass(frozen=True, slots=True)
class DeadlineRow:
	tax_type: str
	period_label: str
	statutory_due_date: date
	due_date: date
	amount: float | None = None
	notes: str = ""


def missing_parameter_fields(group: str, values: Mapping[str, Any]) -> tuple[str, ...]:
	"""Return fields required for a complete national parameter set."""
	if group not in GROUP_PARAMETER_FIELDS:
		return ("single_tax_group",)
	required = COMMON_PARAMETER_FIELDS + GROUP_PARAMETER_FIELDS[group]
	return tuple(fieldname for fieldname in required if values.get(fieldname) in (None, ""))


def previous_working_day(day: date) -> date:
	"""Move Saturday/Sunday backward to the preceding weekday."""
	while day.weekday() >= 5:
		day -= timedelta(days=1)
	return day


def next_working_day(day: date) -> date:
	"""Move Saturday/Sunday forward to the following weekday."""
	while day.weekday() >= 5:
		day += timedelta(days=1)
	return day


def _quarter_end(year: int, quarter: int) -> date:
	last_month = QUARTERS[quarter][1]
	next_month_first = date(year + (1 if last_month == 12 else 0), (last_month % 12) + 1, 1)
	return next_month_first - timedelta(days=1)


def _quarter_payment_due(quarter_end: date, days_after: int) -> tuple[date, date]:
	statutory = quarter_end + timedelta(days=days_after)
	return statutory, next_working_day(statutory)


def build_deadline_rows(
	year: int,
	group: str,
	amounts: TaxAmounts,
	esv: ESVContext | None = None,
) -> tuple[DeadlineRow, ...]:
	"""Build operational deadlines from the statutory calendar-day rules.

	Only weekends are calculated. Exceptional non-operating bank days must be
	checked by the accountant against the current DPS calendar.

	`esv` описує ЄСВ «за себе» саме цього ФОП. Без нього поведінка така сама, як
	до появи пільг: мінімальний внесок за всі чотири квартали.
	"""
	if group not in GROUP_PARAMETER_FIELDS:
		raise ValueError(f"Unsupported single-tax group: {group}")

	rows: list[DeadlineRow] = []
	if group in ("1", "2"):
		rows.extend(_monthly_advance_rows(year, amounts))
		statutory = date(year, 12, 31) + timedelta(days=60)
		rows.append(
			DeadlineRow(
				tax_type="Декларація ЄП",
				period_label=f"{year} рік",
				statutory_due_date=statutory,
				due_date=next_working_day(statutory),
				notes="Річна декларація платника ЄП: 60 к.д. після завершення року.",
			)
		)
	else:
		rows.extend(_third_group_rows(year, amounts))

	rows.extend(_esv_rows(year, amounts.esv_monthly, esv or ESVContext()))
	return tuple(rows)


def esv_period_labels(year: int) -> tuple[str, ...]:
	"""Усі можливі періоди ЄСВ року — для пошуку рядків, які стали зайвими."""
	return tuple(f"{quarter} квартал {year}" for quarter in range(1, 5))


def _monthly_advance_rows(year: int, amounts: TaxAmounts) -> list[DeadlineRow]:
	rows = []
	for month in range(1, 13):
		label = f"{MONTH_NAMES[month - 1]} {year}"
		statutory = date(year, month, 20)
		rows.append(
			DeadlineRow(
				tax_type="Єдиний податок",
				period_label=label,
				statutory_due_date=statutory,
				due_date=previous_working_day(statutory),
				amount=amounts.single_tax_monthly,
				notes=(
					"Авансовий ЄП за підтвердженою ставкою цього ФОП. Якщо 20-те — вихідний, "
					"сплатіть у попередній операційний день."
				),
			)
		)
		rows.append(
			DeadlineRow(
				tax_type="Військовий збір",
				period_label=label,
				statutory_due_date=statutory,
				due_date=previous_working_day(statutory),
				amount=amounts.military_levy_monthly,
				notes=(
					"Авансовий внесок ВЗ. Якщо 20-те — вихідний, перенесення вперед немає: "
					"сплатіть у попередній операційний день. Перевірте право на звільнення."
				),
			)
		)
	return rows


def _third_group_rows(year: int, amounts: TaxAmounts) -> list[DeadlineRow]:
	rows = []
	for quarter in range(1, 5):
		quarter_end = _quarter_end(year, quarter)
		label = f"{quarter} квартал {year}"
		declaration_statutory, declaration_due = _quarter_payment_due(quarter_end, 40)
		payment_statutory, payment_due = _quarter_payment_due(quarter_end, 50)
		rows.extend(
			(
				DeadlineRow(
					tax_type="Декларація ЄП",
					period_label=label,
					statutory_due_date=declaration_statutory,
					due_date=declaration_due,
					notes="Квартальна декларація платника ЄП: 40 к.д. після кварталу.",
				),
				DeadlineRow(
					tax_type="Єдиний податок",
					period_label=label,
					statutory_due_date=payment_statutory,
					due_date=payment_due,
					notes=(
						"ЄП за квартал: "
						f"{amounts.single_tax_percent_no_vat:g}% без ПДВ або "
						f"{amounts.single_tax_percent_vat:g}% з ПДВ."
					),
				),
				DeadlineRow(
					tax_type="Військовий збір",
					period_label=label,
					statutory_due_date=payment_statutory,
					due_date=payment_due,
					notes=f"ВЗ {amounts.military_levy_percent:g}% доходу, разом з ЄП.",
				),
			)
		)
	return rows


def _esv_rows(year: int, esv_monthly: float | None, esv: ESVContext) -> list[DeadlineRow]:
	"""Квартальні рядки ЄСВ з урахуванням звільнення цього ФОП.

	Звільнений квартал не отримує рядка взагалі — нульове зобовʼязання і
	відсутнє зобовʼязання це різні речі. Частково звільнений квартал
	нараховується пропорційно місяцях без пільги.
	"""
	monthly = esv.monthly_amount(esv_monthly)
	rows = []
	for quarter in range(1, 5):
		first_month, last_month = QUARTERS[quarter]
		payable_months = [
			month
			for month in range(first_month, last_month + 1)
			if not esv.is_month_exempt(year, month)
		]
		if not payable_months:
			continue

		quarter_end = _quarter_end(year, quarter)
		next_month = (quarter_end.month % 12) + 1
		next_year = quarter_end.year + (1 if quarter == 4 else 0)
		# Закон формулює строк як «до 20 числа», отже останній день — 19-те.
		statutory = date(next_year, next_month, 19)
		rows.append(
			DeadlineRow(
				tax_type="ЄСВ",
				period_label=f"{quarter} квартал {year}",
				statutory_due_date=statutory,
				due_date=next_working_day(statutory),
				amount=round(monthly * len(payable_months), 2) if monthly is not None else None,
				notes=_esv_note(year, esv, payable_months),
			)
		)
	return rows


def _esv_note(year: int, esv: ESVContext, payable_months: list[int]) -> str:
	if len(payable_months) == 3 and esv.monthly_override is None:
		return ESV_MINIMUM_QUARTER_NOTE

	months = ", ".join(f"{MONTH_NAMES[month - 1]} {year}" for month in payable_months)
	basis = (
		"за підтвердженою сумою ЄСВ цього ФОП"
		if esv.monthly_override is not None
		else "за мінімальним внеском"
	)
	note = (
		f"ЄСВ «за себе» {basis} за {len(payable_months)} міс. ({months}); "
		"строк «до 20 числа» означає 19-те."
	)
	if len(payable_months) < 3:
		reason = esv.exemption_reason or "звільнення зафіксовано у профілі ФОП"
		note += f" Решта місяців кварталу звільнена: {reason}."
	return note
