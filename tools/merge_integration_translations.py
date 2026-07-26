#!/usr/bin/env python3
"""Fold the integrations Ukrainian catalog into the central one.

The two applications each shipped their own ``translations/uk.csv``. Frappe
loads one catalog per app, so the consolidation has to merge them. The merge is
deliberately conservative:

- entries that exist only in the integrations catalog are appended, and also
  written to the overrides file so a future catalog rebuild keeps them;
- entries that exist in both with the same translation are left alone;
- entries that disagree keep the central catalog's wording and are written to a
  review file. Silently changing 72 strings that are already on screen in
  production is not a merge decision, it is a translation review.

    python3 tools/merge_integration_translations.py \
        --integration <path>/uk.csv \
        --catalog erpnext_ua/translations/uk.csv \
        --overrides tools/uk_translation_overrides.csv \
        --report docs/integrations/translation-conflicts.md
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

BRACE_PLACEHOLDER = re.compile(r"(?<!\{)\{(?:[A-Za-z_][A-Za-z0-9_.]*|\d+)\}(?!\})")
PRINTF_PLACEHOLDER = re.compile(r"%(?:\([^)]+\))?[#0+\-]*\d*(?:\.\d+)?[diouxXeEfFgGcrs]")
TEMPLATE_PLACEHOLDER = re.compile(r"\$\{[^{}]+\}")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def placeholder_signature(value: str) -> list[str]:
	return sorted(
		BRACE_PLACEHOLDER.findall(value)
		+ PRINTF_PLACEHOLDER.findall(value)
		+ TEMPLATE_PLACEHOLDER.findall(value)
	)


def rejection_reason(source: str, translation: str) -> str:
	"""The catalog invariants asserted by erpnext_ua/tests/test_translations.py."""
	if not source or not translation:
		return "порожнє значення"
	if source == translation:
		return "переклад дублює оригінал"
	if placeholder_signature(source) != placeholder_signature(translation):
		return "не збігаються підстановки"
	if sorted(EMAIL.findall(source)) != sorted(EMAIL.findall(translation)):
		return "не збігаються адреси у прикладі"
	return ""


def read_catalog(path: Path) -> dict[tuple[str, str], str]:
	with path.open(encoding="utf-8") as handle:
		rows = [row for row in csv.reader(handle) if row]
	catalog = {}
	for row in rows:
		source, translation = row[0], row[1]
		context = row[2] if len(row) > 2 else ""
		catalog[(source, context)] = translation
	return catalog


def append_rows(path: Path, rows: list[tuple[str, str, str]]) -> None:
	with path.open("a", encoding="utf-8", newline="") as handle:
		writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
		writer.writerows(rows)


def write_report(
	path: Path,
	conflicts: list[tuple[str, str, str]],
	skipped: list[tuple[str, str, str]],
) -> None:
	lines = [
		"# Розбіжності перекладу після злиття інтеграцій",
		"",
		"Обидва застосунки мали власний `translations/uk.csv`. Для цих рядків",
		"переклади відрізняються. Залишено варіант центрального каталогу",
		"`erpnext_ua/translations/uk.csv` — він уже показується на бойовому сайті,",
		"і міняти його треба свідомо, а не як побічний ефект злиття.",
		"",
		"Щоб прийняти варіант інтеграцій, додайте рядок у",
		"`tools/uk_translation_overrides.csv` і перезберіть каталог.",
		"",
		"| Джерело | Центральний каталог | Каталог інтеграцій |",
		"| --- | --- | --- |",
	]
	for source, central, integration in sorted(conflicts):
		cells = (c.replace("|", "\\|").replace("\n", " ") for c in (source, central, integration))
		lines.append("| {} | {} | {} |".format(*cells))

	if skipped:
		lines += [
			"",
			"## Не перенесено",
			"",
			"Ці рядки порушують інваріанти каталогу (`erpnext_ua/tests/test_translations.py`),",
			"тому в каталог не додані.",
			"",
			"| Джерело | Переклад інтеграцій | Причина |",
			"| --- | --- | --- |",
		]
		for source, translation, reason in sorted(skipped):
			cells = (c.replace("|", "\\|").replace("\n", " ") for c in (source, translation, reason))
			lines.append("| {} | {} | {} |".format(*cells))

	lines.append("")
	path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--integration", type=Path, required=True)
	parser.add_argument("--catalog", type=Path, required=True)
	parser.add_argument("--overrides", type=Path, required=True)
	parser.add_argument("--report", type=Path, required=True)
	args = parser.parse_args()

	catalog = read_catalog(args.catalog)
	incoming = read_catalog(args.integration)

	added: list[tuple[str, str, str]] = []
	conflicts: list[tuple[str, str, str]] = []
	skipped: list[tuple[str, str, str]] = []
	for (source, context), translation in incoming.items():
		current = catalog.get((source, context))
		if current is not None:
			if current != translation:
				conflicts.append((source, current, translation))
			continue
		reason = rejection_reason(source, translation)
		if reason:
			skipped.append((source, translation, reason))
		else:
			added.append((source, translation, context))

	added.sort()
	append_rows(args.catalog, added)
	append_rows(args.overrides, added)
	args.report.parent.mkdir(parents=True, exist_ok=True)
	write_report(args.report, conflicts, skipped)

	print(
		f"added {len(added)} entries, kept {len(conflicts)} existing translations on conflict, "
		f"skipped {len(skipped)} invalid rows; see the report"
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
