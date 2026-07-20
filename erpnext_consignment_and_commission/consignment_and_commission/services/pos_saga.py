"""Pure planning primitives for split POS checkout, retry and compensation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Literal

RelationshipModel = Literal["OWN", "COMMISSION", "CONSIGNMENT"]
FiscalRoute = Literal["FISCAL", "NON_FISCAL"]
RouteStatus = Literal["PLANNED", "DRAFT", "SUBMITTED", "COMPLETED", "COMPENSATED"]


class POSSagaError(ValueError):
    """Raised when a POS saga plan would violate a domain invariant."""


@dataclass(frozen=True, slots=True)
class CartLine:
    row_id: str
    item_code: str
    qty: Decimal
    rate: Decimal
    company: str
    legal_entity: str
    relationship_model: RelationshipModel
    warehouse: str
    lot_name: str
    serial_no: str | None = None
    batch_no: str | None = None

    @property
    def amount(self) -> Decimal:
        return self.qty * self.rate


@dataclass(frozen=True, slots=True)
class RouteKey:
    company: str
    legal_entity: str
    fiscal_route: FiscalRoute


@dataclass(frozen=True, slots=True)
class CheckoutGroup:
    group_id: str
    key: RouteKey
    lines: tuple[CartLine, ...]
    total: Decimal

    @property
    def print_kind(self) -> str:
        return "FISCAL_RECEIPT" if self.key.fiscal_route == "FISCAL" else "NON_FISCAL_GOODS_RECEIPT"


@dataclass(frozen=True, slots=True)
class PaymentTender:
    tender_id: str
    mode_of_payment: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class PaymentAllocation:
    tender_id: str
    mode_of_payment: str
    group_id: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class RouteProgress:
    group_id: str
    status: RouteStatus
    sales_invoice: str | None = None


@dataclass(frozen=True, slots=True)
class SagaAction:
    action: Literal["CREATE_SALES_INVOICE", "SUBMIT_SALES_INVOICE", "CANCEL_SALES_INVOICE", "RELEASE_RESERVATIONS"]
    group_id: str | None = None
    document_name: str | None = None


@dataclass(frozen=True, slots=True)
class ReturnSource:
    allocation_id: str
    original_row_id: str
    lot_name: str
    warehouse: str
    relationship_model: RelationshipModel
    sold_qty: Decimal
    returned_qty: Decimal = Decimal("0")
    serial_no: str | None = None
    batch_no: str | None = None


@dataclass(frozen=True, slots=True)
class ReturnRestoration:
    allocation_id: str
    original_row_id: str
    lot_name: str
    warehouse: str
    relationship_model: RelationshipModel
    qty: Decimal
    serial_no: str | None
    batch_no: str | None


def _route_for_line(
    line: CartLine,
    *,
    fiscal_checkout: bool,
    commission_is_fiscal: bool,
) -> FiscalRoute:
    if not fiscal_checkout:
        return "NON_FISCAL"
    if line.relationship_model == "COMMISSION" and not commission_is_fiscal:
        return "NON_FISCAL"
    return "FISCAL"


def _group_id(order_id: str, key: RouteKey) -> str:
    raw = "|".join((order_id, key.company, key.legal_entity, key.fiscal_route))
    return f"{order_id}:{sha256(raw.encode()).hexdigest()[:16]}"


def split_checkout(
    order_id: str,
    lines: list[CartLine],
    *,
    fiscal_checkout: bool,
    commission_is_fiscal: bool = False,
) -> tuple[CheckoutGroup, ...]:
    """Group a logical cart by Company, legal entity and snapshotted fiscal route."""
    if not order_id:
        raise POSSagaError("POS Order is required")
    if not lines:
        raise POSSagaError("Checkout requires at least one cart line")
    if len({line.row_id for line in lines}) != len(lines):
        raise POSSagaError("Cart row identifiers must be unique")

    grouped: dict[RouteKey, list[CartLine]] = {}
    for line in lines:
        if line.qty <= 0 or line.rate < 0:
            raise POSSagaError(f"Invalid quantity or rate for cart row {line.row_id!r}")
        if not all((line.company, line.legal_entity, line.item_code, line.warehouse, line.lot_name)):
            raise POSSagaError(f"Incomplete routing data for cart row {line.row_id!r}")
        key = RouteKey(
            company=line.company,
            legal_entity=line.legal_entity,
            fiscal_route=_route_for_line(
                line,
                fiscal_checkout=fiscal_checkout,
                commission_is_fiscal=commission_is_fiscal,
            ),
        )
        grouped.setdefault(key, []).append(line)

    groups = []
    for key in sorted(grouped, key=lambda row: (row.company, row.legal_entity, row.fiscal_route)):
        group_lines = tuple(grouped[key])
        groups.append(
            CheckoutGroup(
                group_id=_group_id(order_id, key),
                key=key,
                lines=group_lines,
                total=sum((line.amount for line in group_lines), Decimal("0")),
            )
        )
    return tuple(groups)


def allocate_payment_plan(
    groups: tuple[CheckoutGroup, ...],
    tenders: list[PaymentTender],
) -> tuple[PaymentAllocation, ...]:
    """Deterministically waterfall one logical tender plan across split invoices."""
    if not groups or not tenders:
        raise POSSagaError("Groups and payment tenders are required")
    if len({tender.tender_id for tender in tenders}) != len(tenders):
        raise POSSagaError("Payment tender identifiers must be unique")
    if any(tender.amount <= 0 for tender in tenders):
        raise POSSagaError("Payment tender amounts must be greater than zero")

    checkout_total = sum((group.total for group in groups), Decimal("0"))
    tender_total = sum((tender.amount for tender in tenders), Decimal("0"))
    if checkout_total != tender_total:
        raise POSSagaError(f"Payment total {tender_total} does not match checkout total {checkout_total}")

    remaining_by_group = {group.group_id: group.total for group in groups}
    allocations = []
    for tender in tenders:
        tender_remaining = tender.amount
        for group in groups:
            if tender_remaining <= 0:
                break
            allocated = min(tender_remaining, remaining_by_group[group.group_id])
            if allocated <= 0:
                continue
            allocations.append(
                PaymentAllocation(
                    tender_id=tender.tender_id,
                    mode_of_payment=tender.mode_of_payment,
                    group_id=group.group_id,
                    amount=allocated,
                )
            )
            tender_remaining -= allocated
            remaining_by_group[group.group_id] -= allocated
        if tender_remaining:
            raise POSSagaError(f"Tender {tender.tender_id!r} could not be fully allocated")
    if any(remaining_by_group.values()):
        raise POSSagaError("One or more split invoices remain unpaid")
    return tuple(allocations)


def pending_invoice_actions(
    groups: tuple[CheckoutGroup, ...],
    progress: list[RouteProgress],
) -> tuple[SagaAction, ...]:
    """Return only missing create/submit actions so retries do not duplicate documents."""
    progress_by_group = {row.group_id: row for row in progress}
    actions = []
    for group in groups:
        route = progress_by_group.get(group.group_id)
        if not route:
            actions.append(SagaAction("CREATE_SALES_INVOICE", group_id=group.group_id))
        elif route.status == "DRAFT":
            actions.append(
                SagaAction(
                    "SUBMIT_SALES_INVOICE",
                    group_id=group.group_id,
                    document_name=route.sales_invoice,
                )
            )
    return tuple(actions)


def plan_compensation(progress: list[RouteProgress]) -> tuple[SagaAction, ...]:
    """Cancel submitted route documents in reverse order, then release reservations."""
    actions = [
        SagaAction(
            "CANCEL_SALES_INVOICE",
            group_id=route.group_id,
            document_name=route.sales_invoice,
        )
        for route in reversed(progress)
        if route.status in {"SUBMITTED", "COMPLETED"} and route.sales_invoice
    ]
    actions.append(SagaAction("RELEASE_RESERVATIONS"))
    return tuple(actions)


def plan_return(
    sources: list[ReturnSource],
    requested_qty: dict[str, Decimal],
) -> tuple[ReturnRestoration, ...]:
    """Restore exactly the original lot, warehouse and ownership for returned quantities."""
    sources_by_id = {source.allocation_id: source for source in sources}
    if len(sources_by_id) != len(sources):
        raise POSSagaError("Return sources must have unique allocation identifiers")

    restorations = []
    for allocation_id, qty in requested_qty.items():
        source = sources_by_id.get(allocation_id)
        if not source:
            raise POSSagaError(f"Unknown return allocation {allocation_id!r}")
        available = source.sold_qty - source.returned_qty
        if qty <= 0 or qty > available:
            raise POSSagaError(
                f"Return quantity {qty} exceeds available {available} for allocation {allocation_id!r}"
            )
        if source.serial_no and qty != 1:
            raise POSSagaError("A serialized allocation must be returned as one exact unit")
        restorations.append(
            ReturnRestoration(
                allocation_id=allocation_id,
                original_row_id=source.original_row_id,
                lot_name=source.lot_name,
                warehouse=source.warehouse,
                relationship_model=source.relationship_model,
                qty=qty,
                serial_no=source.serial_no,
                batch_no=source.batch_no,
            )
        )
    return tuple(restorations)
