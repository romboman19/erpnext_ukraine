from __future__ import annotations

import json
from copy import deepcopy
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
REQUIRED_ACCOUNT_TYPES = {
	"Fixed Asset",
	"Accumulated Depreciation",
	"Bank",
	"Cash",
	"Cost of Goods Sold",
	"Depreciation",
	"Payable",
	"Receivable",
	"Round Off",
	"Stock",
	"Stock Adjustment",
	"Stock Received But Not Billed",
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

	groups = template.get("groups") or {}
	if not groups:
		raise ValueError("Template has no root groups")
	for key, group in groups.items():
		if group.get("root_type") not in ROOT_TYPES:
			raise ValueError(f"Invalid root type for group {key}")

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

	account_types = {row.get("account_type") for row in accounts if row.get("account_type")}
	missing_types = sorted(REQUIRED_ACCOUNT_TYPES - account_types)
	if missing_types:
		raise ValueError(f"Template is missing mandatory ERPNext account types: {missing_types}")

	defaults = template.get("defaults") or {}
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
