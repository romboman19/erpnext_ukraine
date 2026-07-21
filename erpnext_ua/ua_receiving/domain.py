"""Pure receiving and retail-price rules shared by services and tests."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP


VAT_20_MULTIPLIER = Decimal("1.20")


def resolve_receipt_warehouse(
	item_warehouse: str | None,
	header_warehouse: str | None,
	manual_warehouse: str | None = None,
) -> str | None:
	"""Prefer the accounting source of truth before an optional UI fallback."""
	return item_warehouse or header_warehouse or manual_warehouse


def suggest_selling_price(
	unit_cost: float | int | Decimal | None,
	markup_percent: float | int | Decimal | None,
	rounding_step: float | int | Decimal | None,
) -> float | None:
	"""Apply markup and round upward so rounding never erodes the requested margin."""
	if unit_cost is None:
		return None
	cost = Decimal(str(unit_cost))
	if cost <= 0:
		return None
	markup = Decimal(str(markup_percent or 0))
	step = Decimal(str(rounding_step or 0))
	value = cost * (Decimal("1") + markup / Decimal("100"))
	if step > 0:
		value = (value / step).to_integral_value(rounding=ROUND_CEILING) * step
	return float(value.quantize(Decimal("0.01")))


def add_vat_20(
	price_without_vat: float | int | Decimal | None,
	precision: int = 2,
) -> float | None:
	"""Return a gross purchase price without creating a separate tax component."""
	if price_without_vat is None:
		return None
	price = Decimal(str(price_without_vat))
	quantum = Decimal("1").scaleb(-max(0, int(precision)))
	return float((price * VAT_20_MULTIPLIER).quantize(quantum, rounding=ROUND_HALF_UP))
