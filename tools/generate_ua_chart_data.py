#!/usr/bin/env python3
"""Generate auditable ERPNext chart data from official Rada HTML exports.

Usage (HTML files are the printable current editions from zakon.rada.gov.ua):

    python3 tools/generate_ua_chart_data.py \
        --full-html /tmp/ua-coa-291.html \
        --simplified-html /tmp/ua-coa-186.html \
        --template full

The generated JSON is committed to the application so production setup never
depends on network access.  This script is intentionally standard-library only.
"""

from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path


FULL_SYNTHETIC_CODES = (
	"10", "11", "12", "13", "14", "15", "16", "17", "18", "19",
	"20", "21", "22", "23", "24", "25", "26", "27", "28",
	"30", "31", "33", "34", "35", "36", "37", "38", "39",
	"40", "41", "42", "43", "44", "45", "46", "47", "48", "49",
	"50", "51", "52", "53", "54", "55",
	"60", "61", "62", "63", "64", "65", "66", "67", "68", "69",
	"70", "71", "72", "73", "74", "76", "79",
	"80", "81", "82", "83", "84",
	"90", "91", "92", "93", "94", "95", "96", "97", "98",
	"01", "02", "03", "04", "05", "06", "07", "08", "09",
)

SIMPLIFIED_SYNTHETIC_ACCOUNTS = (
	("10", "Основні засоби"),
	("13", "Знос (амортизація) необоротних активів"),
	("14", "Довгострокові фінансові інвестиції"),
	("15", "Капітальні інвестиції"),
	("16", "Довгострокові біологічні активи"),
	("18", "Інші необоротні активи"),
	("20", "Виробничі запаси"),
	("21", "Поточні біологічні активи"),
	("23", "Виробництво"),
	("26", "Готова продукція"),
	("30", "Готівка"),
	("31", "Рахунки в банках"),
	("35", "Поточні фінансові інвестиції"),
	("37", "Розрахунки з різними дебіторами"),
	("39", "Витрати майбутніх періодів"),
	("40", "Власний капітал"),
	("44", "Нерозподілені прибутки (непокриті збитки)"),
	("47", "Забезпечення майбутніх витрат і платежів"),
	("48", "Цільове фінансування і цільові надходження"),
	("55", "Інші довгострокові зобов'язання"),
	("64", "Розрахунки за податками й платежами"),
	("66", "Розрахунки з оплати праці"),
	("68", "Розрахунки за іншими операціями"),
	("69", "Доходи майбутніх періодів"),
	("70", "Доходи від реалізації"),
	("74", "Інші доходи"),
	("79", "Фінансові результати"),
	("90", "Собівартість реалізації"),
	("91", "Загальновиробничі витрати"),
	("97", "Інші витрати"),
)

GROUPS = {
	"1": {"name": "Клас 1. Необоротні активи", "root_type": "Asset"},
	"2": {"name": "Клас 2. Запаси", "root_type": "Asset"},
	"3": {"name": "Клас 3. Кошти, розрахунки та інші активи", "root_type": "Asset"},
	"4e": {"name": "Клас 4. Власний капітал", "root_type": "Equity"},
	"4l": {"name": "Клас 4. Забезпечення та цільове фінансування", "root_type": "Liability"},
	"5": {"name": "Клас 5. Довгострокові зобов'язання", "root_type": "Liability"},
	"6": {"name": "Клас 6. Поточні зобов'язання", "root_type": "Liability"},
	"7": {"name": "Клас 7. Доходи і результати діяльності", "root_type": "Income"},
	"8": {"name": "Клас 8. Витрати за елементами", "root_type": "Expense"},
	"9": {"name": "Клас 9. Витрати діяльності", "root_type": "Expense"},
	"0": {"name": "Клас 0. Позабалансові рахунки", "root_type": "Asset", "off_balance": True},
}

FULL_ACCOUNT_TYPES = {
	"104": "Fixed Asset",
	"131": "Accumulated Depreciation",
	"151": "Capital Work in Progress",
	"281": "Stock",
	"301": "Cash",
	"311": "Bank",
	"641": "Tax",
	"702": "Income Account",
	"791": "Temporary",
	"831": "Depreciation",
	"902": "Cost of Goods Sold",
	"947": "Stock Adjustment",
}

FULL_EXTENSIONS = (
	{
		"code": "3611",
		"name": "Розрахунки з покупцями (операційний рахунок ERPNext)",
		"parent": "361",
		"account_type": "Receivable",
	},
	{
		"code": "3711",
		"name": "Аванси, видані постачальникам (операційний рахунок ERPNext)",
		"parent": "371",
		"account_type": "Receivable",
	},
	{
		"code": "6311",
		"name": "Розрахунки з постачальниками (операційний рахунок ERPNext)",
		"parent": "631",
		"account_type": "Payable",
	},
	{
		"code": "6312",
		"name": "Запаси отримано, рахунок постачальника не отримано",
		"parent": "631",
		"account_type": "Stock Received But Not Billed",
	},
	{
		"code": "6313",
		"name": "Необоротні активи отримано, рахунок постачальника не отримано",
		"parent": "631",
		"account_type": "Asset Received But Not Billed",
	},
	{
		"code": "6314",
		"name": "Послуги отримано, рахунок постачальника не отримано",
		"parent": "631",
		"account_type": "Service Received But Not Billed",
	},
	{
		"code": "6811",
		"name": "Аванси, отримані від покупців (операційний рахунок ERPNext)",
		"parent": "681",
		"account_type": "Payable",
	},
	{"code": "9491", "name": "Округлення", "parent": "949", "account_type": "Round Off"},
	{"code": "9492", "name": "Списання та інші операційні витрати", "parent": "949", "account_type": "Expense Account"},
	{"code": "9493", "name": "Округлення початкових залишків", "parent": "949", "account_type": "Round Off for Opening"},
)

SIMPLIFIED_EXTENSIONS = (
	{
		"code": "10.1",
		"name": "Основні засоби (операційний рахунок ERPNext)",
		"parent": "10",
		"account_type": "Fixed Asset",
	},
	{"code": "26.1", "name": "Товари та готова продукція", "parent": "26", "account_type": "Stock"},
	{"code": "30.1", "name": "Готівка в національній валюті", "parent": "30", "account_type": "Cash"},
	{"code": "31.1", "name": "Поточний рахунок у національній валюті", "parent": "31", "account_type": "Bank"},
	{"code": "37.1", "name": "Розрахунки з покупцями", "parent": "37", "account_type": "Receivable"},
	{"code": "37.2", "name": "Аванси, видані постачальникам", "parent": "37", "account_type": "Receivable"},
	{"code": "68.1", "name": "Розрахунки з постачальниками", "parent": "68", "account_type": "Payable"},
	{
		"code": "68.2",
		"name": "Запаси отримано, рахунок постачальника не отримано",
		"parent": "68",
		"account_type": "Stock Received But Not Billed",
	},
	{
		"code": "68.3",
		"name": "Необоротні активи отримано, рахунок постачальника не отримано",
		"parent": "68",
		"account_type": "Asset Received But Not Billed",
	},
	{
		"code": "68.4",
		"name": "Послуги отримано, рахунок постачальника не отримано",
		"parent": "68",
		"account_type": "Service Received But Not Billed",
	},
	{"code": "68.5", "name": "Аванси, отримані від покупців", "parent": "68", "account_type": "Payable"},
	{
		"code": "70.1",
		"name": "Дохід від реалізації товарів, робіт і послуг",
		"parent": "70",
		"account_type": "Income Account",
	},
	{"code": "70.2", "name": "Знижки та вирахування з доходу", "parent": "70", "account_type": "Income Account"},
	{
		"code": "90.1",
		"name": "Собівартість реалізованих товарів, робіт і послуг",
		"parent": "90",
		"account_type": "Cost of Goods Sold",
	},
	{"code": "97.1", "name": "Коригування запасів", "parent": "97", "account_type": "Stock Adjustment"},
	{"code": "97.2", "name": "Округлення", "parent": "97", "account_type": "Round Off"},
	{"code": "97.3", "name": "Списання та інші витрати", "parent": "97", "account_type": "Expense Account"},
	{"code": "97.4", "name": "Амортизаційні витрати", "parent": "97", "account_type": "Depreciation"},
	{"code": "97.5", "name": "Округлення початкових залишків", "parent": "97", "account_type": "Round Off for Opening"},
)


class _TextExtractor(HTMLParser):
	def __init__(self):
		super().__init__()
		self.parts: list[str] = []

	def handle_data(self, data: str) -> None:
		if data.strip():
			self.parts.append(data.strip())


def _text(path: Path) -> str:
	parser = _TextExtractor()
	parser.feed(path.read_text(encoding="utf-8"))
	return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _group_for(code: str) -> str:
	if code.startswith("0"):
		return "0"
	if code.startswith("4"):
		return "4e" if int(code[:2]) <= 46 else "4l"
	return code[0]


def _full_accounts(path: Path) -> list[dict]:
	text = _text(path)
	heading_pattern = re.compile(r"Рахунок ([0-9]{2}) [\"«]([^\"»]+)[\"»]")
	headings: dict[str, tuple[int, str]] = {}
	for match in heading_pattern.finditer(text):
		code = match.group(1)
		if code in FULL_SYNTHETIC_CODES and code not in headings:
			headings[code] = (match.start(), re.sub(r"\s+", " ", match.group(2)).strip())

	missing = [code for code in FULL_SYNTHETIC_CODES if code not in headings]
	if missing:
		raise ValueError(f"Missing full-plan headings in official HTML: {missing}")

	accounts: list[dict] = []
	for index, code in enumerate(FULL_SYNTHETIC_CODES):
		start, name = headings[code]
		end = headings[FULL_SYNTHETIC_CODES[index + 1]][0] if index + 1 < len(FULL_SYNTHETIC_CODES) else len(text)
		segment = text[start:end]
		accounts.append({"code": code, "name": name, "group": _group_for(code), "source": "official"})

		marker = re.search(r"має (?:такі )?субрахунки:", segment)
		if not marker:
			continue
		tail = segment[marker.end():]
		stop = re.search(
			r"(?:На субрахунк|Аналітичний облік|Рахунок [0-9]{2} .*? кореспондує|За дебетом|За кредитом|\{Рахунок)",
			tail,
		)
		if stop:
			tail = tail[:stop.start()]
		seen_subcodes: set[str] = set()
		for subcode, subname in re.findall(r"([0-9]{3}) [\"«]([^\"»]+)[\"»]", tail):
			# Official second-order codes preserve the two-digit synthetic
			# account prefix.  This also excludes correspondence-table rows
			# that occur later in a few class-0 sections of the HTML export.
			if not subcode.startswith(code) or subcode in seen_subcodes:
				continue
			seen_subcodes.add(subcode)
			accounts.append(
				{
					"code": subcode,
					"name": re.sub(r"\s+", " ", subname).strip(),
					"parent": code,
					"source": "official",
				}
			)

	for account in accounts:
		if account["code"] in FULL_ACCOUNT_TYPES:
			account["account_type"] = FULL_ACCOUNT_TYPES[account["code"]]
	accounts.extend({**row, "source": "erpnext_extension"} for row in FULL_EXTENSIONS)
	return accounts


def _simplified_accounts(full_accounts: list[dict], html_path: Path) -> list[dict]:
	text = _text(html_path)
	for code, name in SIMPLIFIED_SYNTHETIC_ACCOUNTS:
		if not re.search(rf"\|\s*{re.escape(code)}\s*\|", text) and code not in text:
			raise ValueError(f"Simplified account {code} is absent from official HTML")

	accounts = [
		{"code": code, "name": name, "group": _group_for(code), "source": "official"}
		for code, name in SIMPLIFIED_SYNTHETIC_ACCOUNTS
	]
	for account in accounts:
		if account["code"] == "13":
			account["account_type"] = "Accumulated Depreciation"
		elif account["code"] == "15":
			account["account_type"] = "Capital Work in Progress"
		elif account["code"] == "64":
			account["account_type"] = "Tax"
		elif account["code"] == "79":
			account["account_type"] = "Temporary"

	# Order No. 186 explicitly requires class 0 accounts from Plan No. 291.
	accounts.extend(row.copy() for row in full_accounts if row["code"].startswith("0") and row["source"] == "official")
	accounts.extend({**row, "source": "erpnext_extension"} for row in SIMPLIFIED_EXTENSIONS)
	return accounts


def _template(key: str, accounts: list[dict]) -> dict:
	if key == "full_291":
		return {
			"key": key,
			"title": "Україна — повний План рахунків № 291",
			"legal_basis": [
				{
					"document": "Наказ Мінфіну № 291 — План рахунків",
					"url": "https://zakon.rada.gov.ua/go/z0892-99",
					"revision": "2022-07-29",
				},
				{"document": "Інструкція до Плану № 291", "url": "https://zakon.rada.gov.ua/go/z0893-99", "revision": "2025-12-23"},
			],
			"groups": GROUPS,
			"accounts": accounts,
			"defaults": {
				"default_receivable_account": "3611",
				"default_payable_account": "6311",
				"default_cash_account": "301",
				"default_bank_account": "311",
				"default_income_account": "702",
				"default_expense_account": "902",
				"default_inventory_account": "281",
				"stock_received_but_not_billed": "6312",
				"asset_received_but_not_billed": "6313",
				"default_provisional_account": "6314",
				"stock_adjustment_account": "947",
				"round_off_account": "9491",
				"round_off_for_opening": "9493",
				"write_off_account": "9492",
				"default_discount_account": "704",
				"default_deferred_revenue_account": "69",
				"default_deferred_expense_account": "39",
				"default_advance_paid_account": "3711",
				"default_advance_received_account": "6811",
				"accumulated_depreciation_account": "131",
				"depreciation_expense_account": "831",
				"capital_work_in_progress_account": "151",
				"disposal_account": "976",
				"purchase_expense_account": "902",
				"service_expense_account": "903",
			},
		}
	return {
		"key": key,
		"title": "Україна — спрощений План рахунків № 186",
		"legal_basis": [
			{
				"document": "Наказ Мінфіну № 186 — спрощений План рахунків",
				"url": "https://zakon.rada.gov.ua/go/z0389-01",
				"revision": "2025-12-23",
			},
			{"document": "Інструкція до Плану № 291", "url": "https://zakon.rada.gov.ua/go/z0893-99", "revision": "2025-12-23"},
		],
		"groups": GROUPS,
		"accounts": accounts,
		"defaults": {
			"default_receivable_account": "37.1",
			"default_payable_account": "68.1",
			"default_cash_account": "30.1",
			"default_bank_account": "31.1",
			"default_income_account": "70.1",
			"default_expense_account": "90.1",
			"default_inventory_account": "26.1",
			"stock_received_but_not_billed": "68.2",
			"asset_received_but_not_billed": "68.3",
			"default_provisional_account": "68.4",
			"stock_adjustment_account": "97.1",
			"round_off_account": "97.2",
			"round_off_for_opening": "97.5",
			"write_off_account": "97.3",
			"default_discount_account": "70.2",
			"default_deferred_revenue_account": "69",
			"default_deferred_expense_account": "39",
			"default_advance_paid_account": "37.2",
			"default_advance_received_account": "68.5",
			"accumulated_depreciation_account": "13",
			"depreciation_expense_account": "97.4",
			"capital_work_in_progress_account": "15",
			"disposal_account": "97.3",
			"purchase_expense_account": "90.1",
			"service_expense_account": "90.1",
		},
	}


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--full-html", type=Path, required=True)
	parser.add_argument("--simplified-html", type=Path, required=True)
	parser.add_argument("--template", choices=("full", "simplified"), required=True)
	args = parser.parse_args()

	full_accounts = _full_accounts(args.full_html)
	if args.template == "full":
		result = _template("full_291", full_accounts)
	else:
		result = _template("simplified_186", _simplified_accounts(full_accounts, args.simplified_html))
	print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))


if __name__ == "__main__":
	main()
