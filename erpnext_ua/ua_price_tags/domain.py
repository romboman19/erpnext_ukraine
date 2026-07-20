"""Pure price-tag rules shared by the API and unit tests."""

from __future__ import annotations

import math


STANDARD = "Standard"
PROMOTIONAL = "Promotional"
MAX_COPIES_PER_ITEM = 1000
MAX_LABELS_PER_JOB = 5000


def choose_price(regular_price: float | None, promotional_price: float | None) -> tuple[str, float | None]:
	"""Use a promotion only when it is positive and lower than the regular price."""
	if regular_price is None or regular_price <= 0:
		return STANDARD, None
	if promotional_price is not None and 0 < promotional_price < regular_price:
		return PROMOTIONAL, promotional_price
	return STANDARD, regular_price


def copies_for(mode: str, source_qty: float | None, requested: int | float | None) -> int:
	"""Resolve label copies without ever returning zero or a negative number."""
	if mode in {"Quantity", "Source Quantity"}:
		return min(MAX_COPIES_PER_ITEM, max(1, math.ceil(abs(float(source_qty or 0)))))
	if mode in {"Manual", "Manual Copies"}:
		try:
			return min(MAX_COPIES_PER_ITEM, max(1, int(requested or 1)))
		except (TypeError, ValueError):
			return 1
	return 1


def job_group_key(snapshot: dict, label_size: str) -> tuple[str, str, str]:
	"""One packet always has one warehouse, one template, and one label size."""
	return (
		str(snapshot.get("warehouse") or ""),
		str(snapshot.get("template_type") or STANDARD),
		str(label_size),
	)
