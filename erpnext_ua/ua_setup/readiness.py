"""Чи готовий сайт працювати в Україні — і чого саме бракує.

Встановлення застосунку створює все, що не залежить від конкретного суб'єкта:
переклад, податкові параметри року, засоби оплати, ролі, друковані форми,
кастомні поля. Решта — назва ФОП, РНОКПП, група єдиного податку, план рахунків,
склад, каса, ПРРО — це дані платника, які не можна вигадати за нього.

Модуль розділений навмисно: ``evaluate`` — чиста функція над зібраним станом, її
покривають звичайні unit-тести без сайту; ``collect_state`` — єдине місце, що
читає базу.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

REQUIRED_COUNTRY = "Ukraine"
REQUIRED_CURRENCY = "UAH"
REQUIRED_LANGUAGE = "uk"


class Status(StrEnum):
	DONE = "done"
	PENDING = "pending"
	BLOCKED = "blocked"


class Severity(StrEnum):
	#: Без цього не можна провести жодного продажу.
	REQUIRED = "required"
	#: Потрібне лише для фіскальних чеків (роздріб за готівку/картку).
	FISCAL = "fiscal"
	#: Бажане, але не блокує роботу.
	RECOMMENDED = "recommended"


@dataclass(frozen=True)
class Check:
	step: str
	title: str
	status: Status
	severity: Severity
	detail: str = ""
	#: Крок майстра, який це виправляє; порожнє — виправляється вручну.
	fix_action: str = ""


@dataclass(frozen=True)
class SetupState:
	"""Плоскі факти про сайт. Жодних документів Frappe, щоб логіка була чистою."""

	company: str = ""
	company_country: str = ""
	company_currency: str = ""
	system_language: str = ""
	chart_template: str = ""
	company_has_gl_entries: bool = False
	tax_parameter_years: frozenset[int] = frozenset()
	current_year: int = 0
	active_fop_profiles: int = 0
	fop_has_kved: bool = False
	warehouses: int = 0
	retail_customers: int = 0
	active_cash_desks: int = 0
	pos_cash_payment_methods: int = 0
	pos_cashless_payment_methods: int = 0
	prro_registers: int = 0
	prro_registers_with_device_id: int = 0
	kep_keys: int = 0
	prro_signer_configured: bool = False
	print_formats: int = 0
	enabled_connectors: tuple[str, ...] = field(default_factory=tuple)


def evaluate(state: SetupState) -> list[Check]:
	"""Перелік кроків у порядку, у якому їх треба закривати."""
	return [
		_company(state),
		_language(state),
		_chart(state),
		_tax_parameters(state),
		_fop_profile(state),
		_kved(state),
		_warehouse(state),
		_retail_customer(state),
		_cash_desk(state),
		_payment_methods(state),
		_prro_register(state),
		_prro_signing(state),
		_print_formats(state),
	]


def blocking(checks: list[Check]) -> list[Check]:
	return [c for c in checks if c.severity is Severity.REQUIRED and c.status is not Status.DONE]


def can_sell(checks: list[Check]) -> bool:
	return not blocking(checks)


def can_fiscalize(checks: list[Check]) -> bool:
	unmet = [
		c
		for c in checks
		if c.severity in (Severity.REQUIRED, Severity.FISCAL) and c.status is not Status.DONE
	]
	return not unmet


def _company(state: SetupState) -> Check:
	if not state.company:
		return Check(
			"company",
			"Компанія",
			Status.PENDING,
			Severity.REQUIRED,
			"Немає жодної компанії. Створіть її у стандартному майстрі ERPNext.",
		)
	problems = []
	if state.company_country != REQUIRED_COUNTRY:
		problems.append(f"країна {state.company_country or '—'}, потрібна {REQUIRED_COUNTRY}")
	if state.company_currency != REQUIRED_CURRENCY:
		problems.append(f"валюта {state.company_currency or '—'}, потрібна {REQUIRED_CURRENCY}")
	if not problems:
		return Check("company", "Компанія", Status.DONE, Severity.REQUIRED, state.company)
	# Країну й валюту наявної компанії застосунок не переписує: після першої
	# проводки це вже переоцінка обліку, а не налаштування.
	return Check(
		"company",
		"Компанія",
		Status.BLOCKED if state.company_has_gl_entries else Status.PENDING,
		Severity.REQUIRED,
		"; ".join(problems),
	)


def _language(state: SetupState) -> Check:
	if state.system_language == REQUIRED_LANGUAGE:
		return Check("language", "Мова інтерфейсу", Status.DONE, Severity.RECOMMENDED, "українська")
	return Check(
		"language",
		"Мова інтерфейсу",
		Status.PENDING,
		Severity.RECOMMENDED,
		f"System Settings: {state.system_language or '—'}. Каталог перекладу вже встановлений.",
		fix_action="apply_language",
	)


def _chart(state: SetupState) -> Check:
	if state.chart_template:
		return Check("chart", "План рахунків", Status.DONE, Severity.REQUIRED, state.chart_template)
	if state.company_has_gl_entries:
		return Check(
			"chart",
			"План рахунків",
			Status.BLOCKED,
			Severity.REQUIRED,
			"У компанії вже є проводки: заміна плану рахунків — окремий проєкт "
			"вхідних залишків, а не крок налаштування.",
		)
	return Check(
		"chart",
		"План рахунків",
		Status.PENDING,
		Severity.REQUIRED,
		"Виберіть 291 (повний) або 186 (спрощений) і підтвердіть заміну.",
		fix_action="apply_chart",
	)


def _tax_parameters(state: SetupState) -> Check:
	if state.current_year in state.tax_parameter_years:
		return Check(
			"tax_parameters",
			"Податкові параметри",
			Status.DONE,
			Severity.REQUIRED,
			str(state.current_year),
		)
	return Check(
		"tax_parameters",
		"Податкові параметри",
		Status.PENDING,
		Severity.REQUIRED,
		f"Немає параметрів на {state.current_year} рік: МЗП, ліміт доходу, ЄП, ЄСВ, військовий збір.",
		fix_action="apply_tax_parameters",
	)


def _fop_profile(state: SetupState) -> Check:
	if state.active_fop_profiles:
		return Check(
			"fop_profile",
			"Профіль ФОП",
			Status.DONE,
			Severity.REQUIRED,
			f"активних профілів: {state.active_fop_profiles}",
		)
	return Check(
		"fop_profile",
		"Профіль ФОП",
		Status.PENDING,
		Severity.REQUIRED,
		"Потрібні ПІБ, назва для ПРРО, РНОКПП, група єдиного податку та ставка.",
		fix_action="apply_fop_profile",
	)


def _kved(state: SetupState) -> Check:
	if not state.active_fop_profiles:
		return Check("kved", "КВЕД", Status.PENDING, Severity.RECOMMENDED, "після створення профілю ФОП")
	if state.fop_has_kved:
		return Check("kved", "КВЕД", Status.DONE, Severity.RECOMMENDED)
	return Check(
		"kved",
		"КВЕД",
		Status.PENDING,
		Severity.RECOMMENDED,
		"Основний КВЕД не вказано: контроль дозволених видів діяльності не працює.",
	)


def _warehouse(state: SetupState) -> Check:
	if state.warehouses:
		return Check("warehouse", "Склад", Status.DONE, Severity.REQUIRED, f"складів: {state.warehouses}")
	return Check(
		"warehouse",
		"Склад",
		Status.PENDING,
		Severity.REQUIRED,
		"Потрібен хоча б один склад для каси.",
		fix_action="apply_cash_desk",
	)


def _retail_customer(state: SetupState) -> Check:
	if state.retail_customers:
		return Check("retail_customer", "Роздрібний покупець", Status.DONE, Severity.REQUIRED)
	return Check(
		"retail_customer",
		"Роздрібний покупець",
		Status.PENDING,
		Severity.REQUIRED,
		"Каса виписує чек на замовчуваного покупця.",
		fix_action="apply_cash_desk",
	)


def _cash_desk(state: SetupState) -> Check:
	if state.active_cash_desks:
		return Check(
			"cash_desk",
			"Каса",
			Status.DONE,
			Severity.REQUIRED,
			f"активних кас: {state.active_cash_desks}",
		)
	return Check(
		"cash_desk",
		"Каса",
		Status.PENDING,
		Severity.REQUIRED,
		"Без POS Cash Desk касовий інтерфейс не відкриється.",
		fix_action="apply_cash_desk",
	)


def _payment_methods(state: SetupState) -> Check:
	if state.pos_cash_payment_methods and state.pos_cashless_payment_methods:
		return Check("payment_methods", "Засоби оплати", Status.DONE, Severity.REQUIRED)
	missing = []
	if not state.pos_cash_payment_methods:
		missing.append("готівка")
	if not state.pos_cashless_payment_methods:
		missing.append("безготівкова оплата")
	return Check(
		"payment_methods",
		"Засоби оплати",
		Status.PENDING,
		Severity.REQUIRED,
		"Немає активних у касі: " + ", ".join(missing),
		fix_action="apply_payment_methods",
	)


def _prro_register(state: SetupState) -> Check:
	if not state.prro_registers:
		return Check(
			"prro_register",
			"Каса ПРРО",
			Status.PENDING,
			Severity.FISCAL,
			"Потрібні фіскальний номер, локальний номер, назва й адреса точки з "
			"реєстраційних даних ДПС.",
			fix_action="apply_prro_register",
		)
	if state.prro_registers_with_device_id < state.prro_registers:
		return Check(
			"prro_register",
			"Каса ПРРО",
			Status.PENDING,
			Severity.FISCAL,
			"Не всі каси мають device ID.",
			fix_action="apply_prro_register",
		)
	return Check("prro_register", "Каса ПРРО", Status.DONE, Severity.FISCAL, f"кас: {state.prro_registers}")


def _prro_signing(state: SetupState) -> Check:
	if state.kep_keys and state.prro_signer_configured:
		return Check("prro_signing", "Підпис ПРРО", Status.DONE, Severity.FISCAL)
	missing = []
	if not state.kep_keys:
		missing.append("немає ключа КЕП")
	if not state.prro_signer_configured:
		missing.append("не налаштований сервіс підпису")
	return Check(
		"prro_signing",
		"Підпис ПРРО",
		Status.PENDING,
		Severity.FISCAL,
		"; ".join(missing) + ". Ключі не завантажуються автоматично.",
	)


def _print_formats(state: SetupState) -> Check:
	if state.print_formats:
		return Check(
			"print_formats",
			"Друковані форми",
			Status.DONE,
			Severity.RECOMMENDED,
			f"форм: {state.print_formats}",
		)
	return Check(
		"print_formats",
		"Друковані форми",
		Status.PENDING,
		Severity.RECOMMENDED,
		"Виконайте bench migrate: форми створює хук встановлення.",
	)
