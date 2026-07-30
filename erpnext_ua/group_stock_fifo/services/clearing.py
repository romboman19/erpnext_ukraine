"""Balance-sheet clearing accounts for reallocation (§15.2, §15.3, ADR-005).

The whole point of `MANAGEMENT_REALLOCATION` is that moving stock between two
FOP companies is *not* a sale: no internal margin, no invoice, and no effect on
either company's profit. That only holds if the counter-entry lands on a
balance-sheet account. An expense account would show the source company a loss
and the seller a gain that never happened, so §15.3 and §44 both forbid it —
this module treats that as a hard check, not a preference.

Two accounts per company, not one (ADR-005): the spikes used a single shared
clearing account for convenience, but a single account makes `CLEARING_IMBALANCE`
undetectable by construction — you cannot reconcile a figure against itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe

from .domain import GSFError

COUNTERPARTY_DIMENSION = "Counterparty Accounting Company"

#: §15.3 forbids anything that reaches the profit and loss statement. Asset and
#: Liability are the two roots that do not.
BALANCE_SHEET_ROOTS = frozenset({"Asset", "Liability"})


@dataclass(frozen=True, slots=True)
class ClearingPair:
    """One reallocation's two sides, resolved before anything is posted."""

    source_company: str
    destination_company: str
    due_from_account: str
    due_to_account: str


def clearing_pair(*, company_group: str, source_company: str, destination_company: str) -> ClearingPair:
    """§15.2: source posts to Due From, destination posts to Due To."""
    if source_company == destination_company:
        raise GSFError(
            "A same-company transfer has no clearing side", "MANUAL_REVIEW_REQUIRED"
        )
    return ClearingPair(
        source_company=source_company,
        destination_company=destination_company,
        due_from_account=member_account(
            company_group=company_group,
            company=source_company,
            fieldname="default_due_from_stock_account",
        ),
        due_to_account=member_account(
            company_group=company_group,
            company=destination_company,
            fieldname="default_due_to_stock_account",
        ),
    )


def member_account(*, company_group: str, company: str, fieldname: str) -> str:
    """Read one configured clearing account and prove it cannot touch P&L."""
    account = frappe.db.get_value(
        "GSF Group Member", {"parent": company_group, "company": company}, fieldname
    )
    if not account:
        raise GSFError(
            f"{company} has no {fieldname.replace('_', ' ')} configured in {company_group}",
            "CLEARING_ACCOUNT_MISSING",
        )
    assert_balance_sheet(account, company=company)
    return account


def assert_balance_sheet(account: str, *, company: str) -> None:
    """§15.3 and §44: never an expense account, checked rather than trusted."""
    row = frappe.db.get_value(
        "Account", account, ["company", "root_type", "is_group", "account_type"], as_dict=True
    )
    if not row:
        raise GSFError(f"Clearing account {account} does not exist", "CLEARING_ACCOUNT_MISSING")
    if row.company != company:
        raise GSFError(
            f"Clearing account {account} belongs to {row.company}, not {company}",
            "CLEARING_ACCOUNT_MISSING",
        )
    if row.is_group:
        raise GSFError(
            f"Clearing account {account} is a group account", "CLEARING_ACCOUNT_MISSING"
        )
    if row.root_type not in BALANCE_SHEET_ROOTS:
        raise GSFError(
            f"Clearing account {account} is a {row.root_type} account; reallocation must not "
            "reach profit and loss (§15.3)",
            "CLEARING_ACCOUNT_MISSING",
        )
    if row.account_type == "Stock":
        # ERPNext refuses a Stock-type account as a Stock Entry difference
        # account, so this would fail later with a platform message instead of
        # a §33 code.
        raise GSFError(
            f"Clearing account {account} is a Stock account and cannot carry the difference",
            "CLEARING_ACCOUNT_MISSING",
        )


def counterparty_dimension_field() -> str | None:
    """The ADR-005 dimension fieldname, or None if this site has no such dimension.

    ERPNext 16 **refuses** an Accounting Dimension whose `document_type` is
    `Company` — "Not allowed to create accounting dimension for Company",
    verified on `postest.local`. So the dimension ADR-005 asks for cannot be
    provisioned as written, and the reconciliation key it exists to provide is
    carried by `GSF Reallocation Leg.counterparty_company` (§9.15) instead.

    The lookup stays because a mirror-DocType dimension is the documented way
    forward: the day one is provisioned under this name, postings start
    carrying it without any further change here. Looked up by that exact name
    and nothing else — falling back to "any dimension over Company" would stamp
    the counterparty onto an unrelated dimension, which is worse than nothing.
    """
    if not frappe.db.exists("DocType", "Accounting Dimension"):
        return None
    return frappe.db.get_value(
        "Accounting Dimension", {"name": COUNTERPARTY_DIMENSION, "disabled": 0}, "fieldname"
    )


def counterparty_values(counterparty: str) -> dict[str, str]:
    """The dimension payload to stamp on a clearing posting, if one is configured."""
    fieldname = counterparty_dimension_field()
    return {fieldname: counterparty} if fieldname else {}
