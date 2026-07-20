from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from importlib.resources import files


TEMPLATE_LABELS = {
	"Повний — Наказ №291": "full_291",
	"Спрощений — Наказ №186": "simplified_186",
}

TEMPLATE_FILES = {
	"full_291": "ua_full_291.json",
	"simplified_186": "ua_simplified_186.json",
}

ROOT_TYPES = {"Asset", "Liability", "Equity", "Income", "Expense"}
VALID_ACCOUNT_TYPES = {
	"Accumulated Depreciation",
	"Asset Received But Not Billed",
	"Bank",
	"Capital Work in Progress",
	"Cash",
	"Cost of Goods Sold",
	"Depreciation",
	"Expense Account",
	"Fixed Asset",
	"Income Account",
	"Payable",
	"Receivable",
	"Round Off",
	"Round Off for Opening",
	"Service Received But Not Billed",
	"Stock",
	"Stock Adjustment",
	"Stock Received But Not Billed",
	"Tax",
	"Temporary",
}
REQUIRED_ACCOUNT_TYPES = {
	"Fixed Asset",
	"Accumulated Depreciation",
	"Asset Received But Not Billed",
	"Bank",
	"Capital Work in Progress",
	"Cash",
	"Cost of Goods Sold",
	"Depreciation",
	"Expense Account",
	"Income Account",
	"Payable",
	"Receivable",
	"Round Off",
	"Round Off for Opening",
	"Service Received But Not Billed",
	"Stock",
	"Stock Adjustment",
	"Stock Received But Not Billed",
	"Tax",
	"Temporary",
}
REQUIRED_COMPANY_DEFAULTS = {
	"default_receivable_account",
	"default_payable_account",
	"default_cash_account",
	"default_bank_account",
	"default_income_account",
	"default_expense_account",
	"default_inventory_account",
	"stock_received_but_not_billed",
	"asset_received_but_not_billed",
	"default_provisional_account",
	"stock_adjustment_account",
	"round_off_account",
	"round_off_for_opening",
	"write_off_account",
	"default_discount_account",
	"default_deferred_revenue_account",
	"default_deferred_expense_account",
	"default_advance_paid_account",
	"default_advance_received_account",
	"accumulated_depreciation_account",
	"depreciation_expense_account",
	"capital_work_in_progress_account",
	"disposal_account",
	"purchase_expense_account",
	"service_expense_account",
}


def resolve_template_key(value: str) -> str:
	key = TEMPLATE_LABELS.get(value, value)
	if key not in TEMPLATE_FILES:
		raise ValueError(f"Unknown Ukrainian chart template: {value}")
	return key


def load_template(value: str) -> dict:
	key = resolve_template_key(value)
	path = files("erpnext_ua.ua_accounting.chart_of_accounts").joinpath(TEMPLATE_FILES[key])
	template = json.loads(path.read_text(encoding="utf-8"))
	validate_template(template)
	return template


def validate_template(template: dict) -> None:
	if template.get("key") not in TEMPLATE_FILES:
		raise ValueError("Template key is missing or unsupported")
	if not template.get("legal_basis"):
		raise ValueError("Template must identify its legal basis")
	for source in template["legal_basis"]:
		if not source.get("document") or not source.get("url") or not source.get("revision"):
			raise ValueError("Every legal source must include document, URL and revision")
		if not source["url"].startswith("https://zakon.rada.gov.ua/"):
			raise ValueError(f"Legal source is not an official Rada URL: {source['url']}")
		try:
			date.fromisoformat(source["revision"])
		except ValueError as exc:
			raise ValueError(f"Invalid legal revision date: {source['revision']}") from exc

	groups = template.get("groups") or {}
	if not groups:
		raise ValueError("Template has no root groups")
	for key, group in groups.items():
		if group.get("root_type") not in ROOT_TYPES:
			raise ValueError(f"Invalid root type for group {key}")
	if not groups.get("0", {}).get("off_balance"):
		raise ValueError("Template must identify class 0 as off-balance")

	accounts = template.get("accounts") or []
	by_code: dict[str, dict] = {}
	for account in accounts:
		code = str(account.get("code") or "").strip()
		if not code or not account.get("name"):
			raise ValueError("Every account must have a code and name")
		if code in by_code:
			raise ValueError(f"Duplicate account number: {code}")
		if account.get("source") not in {"official", "erpnext_extension"}:
			raise ValueError(f"Missing source classification for account {code}")
		if account.get("account_type") and account["account_type"] not in VALID_ACCOUNT_TYPES:
			raise ValueError(f"Invalid ERPNext account type for {code}: {account['account_type']}")
		by_code[code] = account

	children: dict[str, list[dict]] = {}
	sibling_names: dict[str, set[str]] = {}
	for account in accounts:
		code = account["code"]
		parent = account.get("parent")
		if parent:
			if parent not in by_code:
				raise ValueError(f"Account {code} refers to missing parent {parent}")
			children.setdefault(parent, []).append(account)
			bucket = sibling_names.setdefault(parent, set())
		else:
			group = account.get("group")
			if group not in groups:
				raise ValueError(f"Account {code} refers to missing root group {group}")
			bucket = sibling_names.setdefault(f"group:{group}", set())
		if account["name"] in bucket:
			raise ValueError(f"Duplicate sibling name under {parent or account.get('group')}: {account['name']}")
		bucket.add(account["name"])

	for code in children:
		if by_code[code].get("account_type"):
			raise ValueError(f"Group account {code} cannot have an ERPNext account type")

	def root_group(code: str, path: set[str] | None = None) -> str:
		path = set(path or ())
		if code in path:
			raise ValueError(f"Circular account hierarchy at {code}")
		path.add(code)
		row = by_code[code]
		return root_group(row["parent"], path) if row.get("parent") else row["group"]

	for code, account in by_code.items():
		group = root_group(code)
		if code.startswith("0") and group != "0":
			raise ValueError(f"Class 0 account {code} is outside the off-balance root")
		if group == "0" and account["source"] != "official":
			raise ValueError(f"Template cannot present extension {code} as a statutory class 0 account")

	account_types = {row.get("account_type") for row in accounts if row.get("account_type")}
	missing_types = sorted(REQUIRED_ACCOUNT_TYPES - account_types)
	if missing_types:
		raise ValueError(f"Template is missing mandatory ERPNext account types: {missing_types}")

	defaults = template.get("defaults") or {}
	missing_defaults = sorted(REQUIRED_COMPANY_DEFAULTS - set(defaults))
	if missing_defaults:
		raise ValueError(f"Template is missing mandatory Company defaults: {missing_defaults}")
	unknown_defaults = sorted(set(defaults) - REQUIRED_COMPANY_DEFAULTS)
	if unknown_defaults:
		raise ValueError(f"Template contains unsupported Company defaults: {unknown_defaults}")
	for fieldname, code in defaults.items():
		if code not in by_code:
			raise ValueError(f"Default {fieldname} refers to missing account {code}")
		if code in children:
			raise ValueError(f"Default {fieldname} refers to group account {code}")


def build_erpnext_tree(value: str | dict) -> dict:
	template = deepcopy(value if isinstance(value, dict) else load_template(value))
	validate_template(template)
	accounts = template["accounts"]
	by_parent: dict[str, list[dict]] = {}
	for account in accounts:
		by_parent.setdefault(account.get("parent") or f"group:{account['group']}", []).append(account)

	def build_account(account: dict) -> dict:
		children = by_parent.get(account["code"], [])
		node = {"account_number": account["code"]}
		if children:
			node["is_group"] = 1
		if account.get("account_type"):
			node["account_type"] = account["account_type"]
		for child in children:
			node[child["name"]] = build_account(child)
		return node

	tree = {}
	for group_key, group in template["groups"].items():
		root_children = by_parent.get(f"group:{group_key}", [])
		if not root_children:
			continue
		root = {"root_type": group["root_type"], "is_group": 1}
		for account in root_children:
			root[account["name"]] = build_account(account)
		tree[group["name"]] = root
	return tree


def template_summary(value: str | dict) -> dict:
	template = value if isinstance(value, dict) else load_template(value)
	accounts = template["accounts"]
	return {
		"key": template["key"],
		"title": template["title"],
		"account_count": len(accounts),
		"official_account_count": sum(row["source"] == "official" for row in accounts),
		"erpnext_extension_count": sum(row["source"] == "erpnext_extension" for row in accounts),
		"legal_basis": template["legal_basis"],
		"defaults": template["defaults"],
	}
