"""Generation and maintenance of the FOP tax calendar."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

import frappe
from frappe import _

from erpnext_ua.ua_fop.tax_rules import (
	TaxAmounts,
	build_deadline_rows,
	is_official_source,
	missing_parameter_fields,
	official_source_urls,
)

PARAMETER_LABELS = {
	"minimum_wage": "мінімальна зарплата",
	"subsistence_minimum": "прожитковий мінімум для працездатних осіб",
	"income_limit": "ліміт доходу",
	"single_tax_monthly": "максимальний ЄП на місяць",
	"single_tax_percent_no_vat": "ставка ЄП без ПДВ",
	"single_tax_percent_vat": "ставка ЄП з ПДВ",
	"military_levy_monthly": "військовий збір на місяць",
	"military_levy_percent": "ставка військового збору",
	"esv_monthly": "мінімальний ЄСВ на місяць",
	"official_sources": "офіційні джерела",
	"verified_on": "дата перевірки",
}


class FOPTaxRateConfigurationError(frappe.ValidationError):
	pass


def _get_params(year: int, group: str):
	name = frappe.db.exists("UA Tax Parameters", {"year": year, "single_tax_group": group})
	if not name:
		frappe.throw(
			_("Немає податкових параметрів на {0} рік для групи {1}. Календар не створено.").format(
				year, group
			)
		)
	params = frappe.get_doc("UA Tax Parameters", name)
	missing = missing_parameter_fields(group, params.as_dict())
	if missing:
		labels = ", ".join(PARAMETER_LABELS.get(fieldname, fieldname) for fieldname in missing)
		frappe.throw(
			_("Податкові параметри {0} неповні ({1}). Календар не створено.").format(
				params.name, labels
			)
		)
	return params


def _fixed_rate_provenance(fop, year: int, params) -> dict:
	if fop.single_tax_group not in ("1", "2"):
		return {
			"fop_tax_rate_year": None,
			"fop_tax_rate_verified_on": None,
			"fop_tax_rate_sources": None,
		}
	fields = (
		"single_tax_rate_year",
		"single_tax_monthly_amount",
		"single_tax_rate_verified_on",
		"single_tax_rate_sources",
	)
	missing = [fieldname for fieldname in fields if fop.get(fieldname) in (None, "")]
	if missing or int(fop.single_tax_rate_year or 0) != year:
		frappe.throw(
			_("Для {0} не підтверджено фактичну ставку ЄП на {1} рік. Календар не створено.").format(
				fop.name, year
			),
			FOPTaxRateConfigurationError,
		)
	sources = official_source_urls(fop.single_tax_rate_sources)
	if not sources or any(not is_official_source(source) for source in sources):
		frappe.throw(
			_("Для {0} вкажіть офіційне gov.ua джерело фактичної ставки ЄП").format(fop.name),
			FOPTaxRateConfigurationError,
		)
	if frappe.utils.flt(fop.single_tax_monthly_amount, 2) > frappe.utils.flt(
		params.single_tax_monthly, 2
	):
		frappe.throw(
			_("Фактична ставка ЄП для {0} перевищує максимум року").format(fop.name),
			FOPTaxRateConfigurationError,
		)
	return {
		"fop_tax_rate_year": year,
		"fop_tax_rate_verified_on": fop.single_tax_rate_verified_on,
		"fop_tax_rate_sources": "\n".join(sources),
	}


def _rows_for_group(year: int, fop):
	group = fop.single_tax_group
	params = _get_params(year, group)
	rate_provenance = _fixed_rate_provenance(fop, year, params)
	amounts = TaxAmounts(
		single_tax_monthly=(
			fop.single_tax_monthly_amount if group in ("1", "2") else params.single_tax_monthly
		),
		military_levy_monthly=params.military_levy_monthly,
		esv_monthly=params.esv_monthly,
		single_tax_percent_no_vat=params.single_tax_percent_no_vat,
		single_tax_percent_vat=params.single_tax_percent_vat,
		military_levy_percent=params.military_levy_percent,
	)
	return params, rate_provenance, build_deadline_rows(year, group, amounts)


@frappe.whitelist(methods=["POST"])
def generate_deadlines(fop_profile: str, year: int | None = None) -> dict:
	"""Create or refresh deadlines for one FOP and year."""
	frappe.only_for(("System Manager", "Accounts Manager"))
	frappe.get_doc("FOP Profile", fop_profile).check_permission("read")
	return _generate_deadlines(fop_profile, year)


def _generate_deadlines(fop_profile: str, year: int | None = None) -> dict:
	year = int(year) if year else frappe.utils.getdate().year
	fop = frappe.get_doc("FOP Profile", fop_profile)
	params, rate_provenance, rows = _rows_for_group(year, fop)
	created = updated = skipped = 0
	for rule in rows:
		row = asdict(rule)
		existing = frappe.db.exists(
			"UA Tax Deadline",
			{
				"fop_profile": fop.name,
				"tax_type": row["tax_type"],
				"period_label": row["period_label"],
			},
		)
		if not existing:
			existing = frappe.db.exists(
				"UA Tax Deadline",
				{
					"company": fop.company,
					"tax_type": row["tax_type"],
					"period_label": row["period_label"],
				},
			)
		if existing:
			changed = _refresh_deadline(existing, row, params.name, rate_provenance, fop)
			updated += int(changed)
			skipped += int(not changed)
			continue

		doc = frappe.new_doc("UA Tax Deadline")
		doc.update(row)
		doc.company = fop.company
		doc.fop_profile = fop.name
		doc.tax_parameters = params.name
		doc.update(rate_provenance)
		doc.insert(ignore_permissions=True)
		created += 1
	frappe.db.commit()
	return {"created": created, "updated": updated, "skipped": skipped, "year": year}


def _refresh_deadline(
	name: str,
	row: dict,
	tax_parameters: str,
	rate_provenance: dict,
	fop,
) -> bool:
	doc = frappe.get_doc("UA Tax Deadline", name)
	if doc.status == "Виконано":
		provenance = {}
		if not doc.statutory_due_date:
			provenance["statutory_due_date"] = row["statutory_due_date"]
		if not doc.tax_parameters:
			provenance["tax_parameters"] = tax_parameters
		if not doc.fop_profile:
			provenance["fop_profile"] = fop.name
		for fieldname, value in rate_provenance.items():
			if value is not None and not doc.get(fieldname):
				provenance[fieldname] = value
		if provenance:
			frappe.db.set_value("UA Tax Deadline", doc.name, provenance, update_modified=False)
		return bool(provenance)

	due_date_changed = frappe.utils.getdate(doc.due_date) != row["due_date"]
	values = {
		**row,
		**rate_provenance,
		"company": fop.company,
		"fop_profile": fop.name,
		"tax_parameters": tax_parameters,
	}
	changed = due_date_changed or any(
		_not_equal(doc.get(fieldname), value, fieldname) for fieldname, value in values.items()
	)
	if not changed:
		return False

	doc.update(values)
	if due_date_changed:
		doc.status = "Заплановано"
		doc.notified_due_soon = 0
		doc.notified_overdue = 0
	doc.save(ignore_permissions=True)
	return True


def _not_equal(current, expected, fieldname: str) -> bool:
	if fieldname in ("due_date", "statutory_due_date"):
		return frappe.utils.getdate(current) != expected
	if fieldname == "amount":
		return frappe.utils.flt(current, 2) != frappe.utils.flt(expected, 2)
	return current != expected


def generate_for_all_fops():
	"""Ensure the current calendar, and in December the next calendar, exists."""
	today = frappe.utils.getdate()
	years = (today.year, today.year + 1) if today.month == 12 else (today.year,)
	for name in frappe.get_all("FOP Profile", filters={"status": "Active"}, pluck="name"):
		for year in years:
			try:
				_generate_deadlines(name, year)
			except FOPTaxRateConfigurationError:
				_repair_existing_deadline_dates(name, year)
				frappe.log_error(
					title=f"Потрібна фактична ставка ЄП: {name}, {year}",
					message=frappe.get_traceback(),
				)
			except Exception:
				frappe.log_error(
					title=f"Не створено податковий календар: {name}, {year}",
					message=frappe.get_traceback(),
				)


def _repair_existing_deadline_dates(fop_profile: str, year: int) -> None:
	"""Repair dates without inventing a missing per-FOP fixed-tax amount."""
	fop = frappe.get_doc("FOP Profile", fop_profile)
	params = _get_params(year, fop.single_tax_group)
	rows = build_deadline_rows(
		year,
		fop.single_tax_group,
		TaxAmounts(
			single_tax_monthly=params.single_tax_monthly,
			military_levy_monthly=params.military_levy_monthly,
			esv_monthly=params.esv_monthly,
			single_tax_percent_no_vat=params.single_tax_percent_no_vat,
			single_tax_percent_vat=params.single_tax_percent_vat,
			military_levy_percent=params.military_levy_percent,
		),
	)
	for rule in rows:
		existing = frappe.db.exists(
			"UA Tax Deadline",
			{
				"company": fop.company,
				"tax_type": rule.tax_type,
				"period_label": rule.period_label,
			},
		)
		if not existing:
			continue
		doc = frappe.get_doc("UA Tax Deadline", existing)
		values = {
			"fop_profile": fop.name,
			"tax_parameters": params.name,
			"statutory_due_date": rule.statutory_due_date,
		}
		if doc.status != "Виконано":
			values["due_date"] = rule.due_date
			if frappe.utils.getdate(doc.due_date) != rule.due_date:
				values.update(
					{
						"status": "Заплановано",
						"notified_due_soon": 0,
						"notified_overdue": 0,
					}
				)
		frappe.db.set_value("UA Tax Deadline", doc.name, values, update_modified=False)
	frappe.db.commit()


def update_statuses_and_notify():
	"""Update deadline statuses and send reminders once per state."""
	today = frappe.utils.getdate()
	soon = today + timedelta(days=3)
	open_deadlines = frappe.get_all(
		"UA Tax Deadline",
		filters={"status": ("!=", "Виконано")},
		fields=[
			"name",
			"company",
			"tax_type",
			"period_label",
			"due_date",
			"status",
			"notified_due_soon",
			"notified_overdue",
		],
	)
	for deadline in open_deadlines:
		due = frappe.utils.getdate(deadline.due_date)
		if due < today:
			new_status = "Прострочено"
		elif due <= soon:
			new_status = "Скоро термін"
		else:
			new_status = "Заплановано"
		if new_status != deadline.status:
			frappe.db.set_value(
				"UA Tax Deadline", deadline.name, "status", new_status, update_modified=False
			)

		if new_status == "Скоро термін" and not deadline.notified_due_soon:
			_notify(
				deadline,
				f"До {frappe.utils.formatdate(due, 'dd.MM.yyyy')} — "
				f"{deadline.tax_type} ({deadline.period_label}), {deadline.company}",
			)
			frappe.db.set_value(
				"UA Tax Deadline", deadline.name, "notified_due_soon", 1, update_modified=False
			)
		elif new_status == "Прострочено" and not deadline.notified_overdue:
			_notify(
				deadline,
				f"ПРОСТРОЧЕНО: {deadline.tax_type} ({deadline.period_label}), "
				f"{deadline.company} — строк був {frappe.utils.formatdate(due, 'dd.MM.yyyy')}",
			)
			frappe.db.set_value(
				"UA Tax Deadline", deadline.name, "notified_overdue", 1, update_modified=False
			)
	frappe.db.commit()


def _accounts_users() -> list[str]:
	users = frappe.get_all(
		"Has Role",
		filters={"role": ("in", ["Accounts Manager", "System Manager"]), "parenttype": "User"},
		pluck="parent",
	)
	return [
		user
		for user in set(users)
		if user not in ("Administrator", "Guest") and frappe.db.get_value("User", user, "enabled")
	] or ["Administrator"]


def _notify(deadline, subject: str):
	for user in _accounts_users():
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": "UA Tax Deadline",
				"document_name": deadline.name,
				"subject": subject,
			}
		).insert(ignore_permissions=True)
