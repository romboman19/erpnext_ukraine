"""Дефолт режиму ЄСВ для вже існуючих профілів ФОП.

«Мінімальний внесок» — це поведінка до появи пільг, тому наявні профілі та їх
календарі не змінюють змісту. Хто з власників справді звільнений — заповнює
власник у формі `FOP Profile` після викладки; це дані, не міграція.

Патч ідемпотентний: другий прогін не знаходить порожніх значень і нічого не
пише. Перегенерація календаря теж ідемпотентна — вона лише дописує відсутні
рядки й оновлює строки.
"""

import frappe

from erpnext_ua.ua_fop.tax_rules import ESV_MODE_MINIMUM


def execute():
    if not frappe.db.has_column("FOP Profile", "esv_mode"):
        return

    frappe.db.sql(
        """update `tabFOP Profile`
        set esv_mode = %s
        where coalesce(esv_mode, '') = ''""",
        (ESV_MODE_MINIMUM,),
    )
    frappe.db.commit()

    _attach_esv_legal_source()
    _regenerate_calendars()


def _attach_esv_legal_source():
    """Дописати норму, з якої взято ЄСВ, у джерела наявних наборів параметрів.

    Тільки дописування: нічого з уже внесеного адміністратором не замінюється, а
    повторний прогін нічого не змінює, бо посилання вже на місці.
    """
    from erpnext_ua.install import TAX_SOURCE_ESV_LAW

    rows = frappe.get_all(
        "UA Tax Parameters", fields=["name", "official_sources"], filters={"esv_monthly": (">", 0)}
    )
    for row in rows:
        sources = [line.strip() for line in (row.official_sources or "").splitlines() if line.strip()]
        if TAX_SOURCE_ESV_LAW in sources:
            continue
        frappe.db.set_value(
            "UA Tax Parameters",
            row.name,
            "official_sources",
            "\n".join([*sources, TAX_SOURCE_ESV_LAW]),
            update_modified=False,
        )
    frappe.db.commit()


def _regenerate_calendars():
    """Привести календар у відповідність до нових полів, не ламаючи міграцію."""
    if not frappe.db.exists("DocType", "UA Tax Deadline"):
        return
    try:
        from erpnext_ua.ua_fop.tax_calendar import generate_for_all_fops

        generate_for_all_fops()
    except Exception:
        # Відсутні UA Tax Parameters чи непідтверджена ставка не повинні
        # зривати `bench migrate`; власник перегенерує календар кнопкою.
        frappe.log_error(
            frappe.get_traceback(),
            "Перегенерація податкового календаря після патчу ЄСВ",
        )
