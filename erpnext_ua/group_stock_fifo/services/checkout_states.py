"""The §23 checkout state machine, as pure rules.

A checkout is the only object in GSF that outlives a transaction. Everything
else — an allocation, a reallocation, an invoice — is finished or rolled back
within one call. A checkout spans several, and at least one of them (§23.2,
fiscalization) reaches outside the database entirely, where nothing can be
rolled back at all.

That is why the states are written down here rather than implied by the code
that walks them: the question "what may happen next, given where this got to"
has to be answerable from the row alone, by a recovery job that was not present
when the failure happened.
"""

from __future__ import annotations

from .domain import GSFError

DRAFT = "DRAFT"
RESERVING = "RESERVING"
RESERVED = "RESERVED"
PREPARING_STOCK = "PREPARING_STOCK"
STOCK_PREPARED = "STOCK_PREPARED"
ERP_SALE_SUBMITTED = "ERP_SALE_SUBMITTED"
FISCAL_PENDING = "FISCAL_PENDING"
FISCAL_RETRY = "FISCAL_RETRY"
COMPLETED = "COMPLETED"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"
COMPENSATING = "COMPENSATING"
COMPENSATED = "COMPENSATED"
FAILED = "FAILED"
MANUAL_REVIEW = "MANUAL_REVIEW"
RETURN_IN_PROGRESS = "RETURN_IN_PROGRESS"
RETURNED = "RETURNED"

#: §23. `MANUAL_REVIEW` is reachable from every state that can be uncertain,
#: because an operator has to be able to take over wherever the machine stops
#: being sure — that is what the state is for.
TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({RESERVING, CANCELLED, FAILED}),
    RESERVING: frozenset({RESERVED, FAILED, CANCELLED}),
    RESERVED: frozenset({PREPARING_STOCK, EXPIRED, CANCELLED, COMPENSATING, FAILED}),
    PREPARING_STOCK: frozenset({STOCK_PREPARED, FAILED, COMPENSATING, MANUAL_REVIEW}),
    STOCK_PREPARED: frozenset({ERP_SALE_SUBMITTED, COMPENSATING, FAILED, MANUAL_REVIEW}),
    ERP_SALE_SUBMITTED: frozenset({FISCAL_PENDING, COMPLETED, MANUAL_REVIEW}),
    FISCAL_PENDING: frozenset({COMPLETED, FISCAL_RETRY, MANUAL_REVIEW}),
    FISCAL_RETRY: frozenset({FISCAL_PENDING, COMPLETED, MANUAL_REVIEW}),
    COMPLETED: frozenset({RETURN_IN_PROGRESS}),
    COMPENSATING: frozenset({COMPENSATED, MANUAL_REVIEW}),
    COMPENSATED: frozenset(),
    EXPIRED: frozenset({COMPENSATING, MANUAL_REVIEW}),
    CANCELLED: frozenset(),
    FAILED: frozenset({COMPENSATING, MANUAL_REVIEW}),
    MANUAL_REVIEW: frozenset({COMPENSATING, COMPENSATED, COMPLETED, CANCELLED, FAILED}),
    RETURN_IN_PROGRESS: frozenset({RETURNED, MANUAL_REVIEW}),
    RETURNED: frozenset(),
}

#: States in which stock is sitting in a lane with no sale behind it. Aborting
#: from any of them owes a compensation, not a release (§23.2).
STAGED_STATES = frozenset({PREPARING_STOCK, STOCK_PREPARED})

#: States after which an external side effect may already exist. §14.6 allows a
#: plain rollback only before this line.
IRREVERSIBLE_STATES = frozenset(
    {ERP_SALE_SUBMITTED, FISCAL_PENDING, FISCAL_RETRY, COMPLETED, RETURN_IN_PROGRESS, RETURNED}
)

TERMINAL_STATES = frozenset({COMPLETED, CANCELLED, COMPENSATED, RETURNED})


def validate_transition(current: str, target: str) -> None:
    if current not in TRANSITIONS or target not in TRANSITIONS:
        raise GSFError(
            f"Unsupported checkout transition {current} → {target}", "MANUAL_REVIEW_REQUIRED"
        )
    if current == target:
        return
    if target not in TRANSITIONS[current]:
        raise GSFError(
            f"Checkout cannot move from {current} to {target}", "MANUAL_REVIEW_REQUIRED"
        )


def needs_compensation(status: str) -> bool:
    """§23.2: whether aborting from here has to reverse posted documents."""
    return status in STAGED_STATES


def is_reversible(status: str) -> bool:
    """Whether a plain rollback is still enough (§14.6)."""
    return status not in IRREVERSIBLE_STATES


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATES


def next_step(status: str) -> str | None:
    """What a recovery pass should do next with a checkout in this state.

    Returns the *action*, not the state, because the action is what a resume
    has to be able to repeat safely: each one is idempotent, so re-running a
    step that already half-happened converges instead of double-posting.
    """
    return {
        DRAFT: "reserve",
        RESERVING: "reserve",
        RESERVED: "prepare",
        PREPARING_STOCK: "prepare",
        STOCK_PREPARED: "sell",
        ERP_SALE_SUBMITTED: "complete",
        FISCAL_PENDING: "await_fiscal",
        FISCAL_RETRY: "await_fiscal",
        COMPENSATING: "compensate",
    }.get(status)
