from __future__ import annotations

import csv
import re
import unittest
from pathlib import Path


CATALOG = Path(__file__).resolve().parents[1] / "translations" / "uk.csv"
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


class TestUkrainianTranslations(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		with CATALOG.open(encoding="utf-8", newline="") as source:
			cls.rows = list(csv.reader(source))
		cls.translations = {
			(row[0], row[2] if len(row) == 3 else ""): row[1]
			for row in cls.rows
			if len(row) in (2, 3)
		}

	def test_catalog_has_expected_coverage(self):
		self.assertGreaterEqual(len(self.rows), 17_000)

	def test_rows_are_valid_and_unique(self):
		seen = set()
		for row_number, row in enumerate(self.rows, start=1):
			self.assertIn(len(row), (2, 3), f"row {row_number} has {len(row)} columns")
			message, translation = row[:2]
			context = row[2] if len(row) == 3 else ""
			self.assertTrue(message, f"row {row_number} has an empty source")
			self.assertTrue(translation, f"row {row_number} has an empty translation")
			self.assertNotEqual(message, translation, f"row {row_number} is an identity translation")
			self.assertNotIn((message, context), seen, f"duplicate key at row {row_number}")
			seen.add((message, context))

	def test_placeholders_and_example_emails_are_preserved(self):
		for row in self.rows:
			message, translation = row[:2]
			self.assertEqual(
				placeholder_signature(message),
				placeholder_signature(translation),
				f"placeholder mismatch for {message!r}",
			)
			self.assertEqual(
				sorted(EMAIL.findall(message)),
				sorted(EMAIL.findall(translation)),
				f"email mismatch for {message!r}",
			)

	def test_catalog_is_not_a_download_error_page(self):
		content = CATALOG.read_text(encoding="utf-8").lower()
		self.assertNotIn("<!doctype html>", content)
		self.assertNotIn("csrf-token", content)

	def test_representative_erpnext_labels(self):
		expected = {
			"Accounting": "Бухгалтерський облік",
			"Customer": "Клієнт",
			"Sales Invoice": "Рахунок-фактура на продаж",
			"Settings": "Налаштування",
			"Submit": "Провести",
		}
		for message, translation in expected.items():
			self.assertEqual(self.translations[(message, "")], translation)

	def test_prro_labels_are_ukrainian(self):
		expected = {
			"DocType": "Тип документа",
			"UA Fiscal": "Фіскалізація ПРРО",
			"PRRO Cash Register": "Каса ПРРО",
			"PRRO Offline Session": "Офлайн-сесія ПРРО",
			"PRRO Receipt": "Фіскальний документ ПРРО",
			"PRRO Settings": "Налаштування ПРРО",
			"PRRO Shift": "Фіскальна зміна ПРРО",
			"UA KEP Key": "Ключ КЕП",
			"Fiscalized": "Фіскалізовано",
			"Uncertain": "Потребує перевірки",
			"Service In": "Службове внесення",
			"Z Report": "Z-звіт",
		}
		for message, translation in expected.items():
			self.assertEqual(self.translations[(message, "")], translation)


if __name__ == "__main__":
	unittest.main()
