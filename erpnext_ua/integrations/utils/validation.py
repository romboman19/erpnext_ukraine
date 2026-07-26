from __future__ import annotations

import re
from urllib.parse import urlparse

import frappe
from frappe import _

_BARE_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$"
)


def validate_profile_rows(rows, label: str) -> None:
    enabled = [row for row in (rows or []) if int(row.get("enabled") or 0) == 1]
    defaults = [row for row in enabled if int(row.get("is_default") or 0) == 1]
    labels = [(row.get("label") or "").strip().casefold() for row in (rows or [])]
    labels = [value for value in labels if value]
    if len(defaults) > 1:
        frappe.throw(_("{0}: only one enabled default profile is allowed").format(label))
    if len(labels) != len(set(labels)):
        frappe.throw(_("{0}: profile labels must be unique").format(label))


def validate_http_url(value: str, label: str, *, allow_http: bool = False) -> None:
    parsed = urlparse((value or "").strip())
    allowed = {"https"} | ({"http"} if allow_http else set())
    if parsed.scheme not in allowed or not parsed.hostname or parsed.username or parsed.password:
        frappe.throw(_("{0} must be a valid {1} URL without embedded credentials").format(label, "/".join(sorted(allowed))))


def validate_allowed_host(
    value: str,
    label: str,
    *,
    default_hosts: set[str],
    config_key: str,
) -> None:
    hostname = (urlparse((value or "").strip()).hostname or "").lower()
    configured = frappe.conf.get(config_key, [])
    if isinstance(configured, str):
        configured = [item.strip() for item in configured.split(",") if item.strip()]
    allowed = {host.lower() for host in default_hosts}
    allowed.update(str(host).strip().lower() for host in (configured or []) if str(host).strip())
    if hostname not in allowed:
        frappe.throw(
            _("{0} host is not allowlisted. Configure {1} in site_config.json").format(label, config_key)
        )


def validate_hostname(hostname: str, label: str) -> str:
    """Normalize and validate a bare hostname without authorizing a connection."""
    normalized = str(hostname or "").strip().rstrip(".").lower()
    if not _BARE_HOSTNAME.fullmatch(normalized):
        frappe.throw(_("{0} must be a valid hostname without credentials or a port").format(label))
    return normalized


def validate_erp_bank_account(bank_account: str, company: str) -> str:
    """Validate company ownership and return the linked ledger account currency."""
    row = frappe.db.get_value(
        "Bank Account",
        bank_account,
        ["company", "account"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("ERP Bank Account does not exist: {0}").format(bank_account))
    if row.company and row.company != company:
        frappe.throw(_("ERP Bank Account belongs to a different company"))
    currency = frappe.db.get_value("Account", row.account, "account_currency") if row.account else None
    if not currency:
        frappe.throw(_("ERP Bank Account must be linked to a ledger account with a currency"))
    return str(currency).upper()
