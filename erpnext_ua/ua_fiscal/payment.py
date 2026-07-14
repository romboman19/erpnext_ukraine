from __future__ import annotations


_PAYFORM_NAMES = {
	0: "ГОТІВКА",
	1: "КАРТКА",
}

_PAYFORM_ALIASES = {
	"cash": "ГОТІВКА",
	"готівка": "ГОТІВКА",
	"card": "КАРТКА",
	"credit card": "КАРТКА",
	"debit card": "КАРТКА",
	"картка": "КАРТКА",
	"банківська картка": "КАРТКА",
	"iban": "ПЕРЕКАЗ НА РАХУНОК",
	"bank transfer": "ПЕРЕКАЗ НА РАХУНОК",
	"bonus": "БОНУСИ",
	"installment": "РОЗСТРОЧКА",
}


def canonical_payform_name(code: int | str | None, name: str | None = None) -> str:
	"""Return a stable Ukrainian fiscal payment name.

	Codes 0/1 have protocol-wide semantics in this app. For custom codes we
	preserve a configured Ukrainian name and translate only known ERPNext/POS
	aliases, avoiding an invented meaning for a merchant-defined code.
	"""
	try:
		numeric_code = int(code) if code not in (None, "") else None
	except (TypeError, ValueError):
		numeric_code = None
	if numeric_code in _PAYFORM_NAMES:
		return _PAYFORM_NAMES[numeric_code]
	normalized = str(name or "").strip()
	if not normalized:
		return "ІНША ФОРМА ОПЛАТИ"
	return _PAYFORM_ALIASES.get(normalized.casefold(), normalized)


def fiscal_payform_name(kind: str | None, code: int | str | None, configured_name: str | None = None) -> str:
	"""Normalize an internal POS payment kind before it enters signed XML."""
	by_kind = {
		"Cash": "ГОТІВКА",
		"Card": "КАРТКА",
		"IBAN": "ПЕРЕКАЗ НА РАХУНОК",
		"Bonus": "БОНУСИ",
		"Installment": "РОЗСТРОЧКА",
	}
	return by_kind.get(str(kind or "")) or canonical_payform_name(code, configured_name)
