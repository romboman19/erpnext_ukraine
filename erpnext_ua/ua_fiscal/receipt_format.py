"""Formatting helpers shared by fiscal receipt persistence and printing."""

from __future__ import annotations

from urllib.parse import urlencode

import frappe


CHECK_LOOKUP_URL = "https://cabinet.tax.gov.ua/cashregs/check"


def build_verification_url(
	register_fiscal_number: str,
	receipt_fiscal_number: str,
	total,
	posting_datetime,
	*,
	mac: str | None = None,
) -> str:
	"""Build the QR lookup query required by the current form No. FKCh-1/FKCh-2."""
	dt = frappe.utils.get_datetime(posting_datetime)
	params = []
	if mac:
		params.append(("mac", str(mac)))
	params.extend(
		[
			("date", dt.strftime("%Y%m%d")),
			("time", dt.strftime("%H%M%S")),
			("id", str(receipt_fiscal_number)),
			("sm", f"{frappe.utils.flt(total):.2f}"),
			("fn", str(register_fiscal_number)),
		]
	)
	return f"{CHECK_LOOKUP_URL}?{urlencode(params)}"


def offline_control_number(fiscal_number: str | None) -> str:
	"""Return the control-number component of an offline fiscal number."""
	parts = str(fiscal_number or "").rsplit(".", 1)
	return parts[1] if len(parts) == 2 else ""
