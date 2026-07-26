"""PSBO account-024 integration backed by erpnext_ua's simple ledger."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def _decimal(value: Any) -> Decimal:
    result = Decimal(str(value or 0))
    if not result.is_finite():
        raise ValueError("Off-balance values must be finite")
    return result


def _currency_precision(frappe: Any) -> int:
    return int(frappe.db.get_single_value("System Settings", "currency_precision") or 2)


def _rounded_amount(frappe: Any, unit_value: Any, qty: Any) -> Decimal:
    quantum = Decimal("1").scaleb(-_currency_precision(frappe))
    return (_decimal(unit_value) * _decimal(qty)).quantize(quantum, rounding=ROUND_HALF_UP)


def _serials(value: str | None) -> tuple[str, ...]:
    return tuple(line.strip() for line in (value or "").splitlines() if line.strip())


def _movement_parts(
    frappe: Any,
    *,
    qty: Decimal,
    amount: Decimal,
    serial_numbers: Iterable[str],
) -> tuple[tuple[Decimal, Decimal, str | None], ...]:
    if qty <= 0 or amount <= 0:
        raise ValueError("Account-024 movement quantity and amount must be positive")
    serials = tuple(serial_numbers)
    if not serials:
        return ((qty, amount, None),)
    if qty != qty.to_integral_value() or int(qty) != len(serials):
        raise ValueError("Serialized account-024 movement requires one Serial No per unit")
    unit_amount = _rounded_amount(frappe, amount / qty, 1)
    parts: list[tuple[Decimal, Decimal, str | None]] = []
    assigned = Decimal("0")
    for index, serial_no in enumerate(serials):
        part_amount = amount - assigned if index == len(serials) - 1 else unit_amount
        if part_amount <= 0:
            raise ValueError(
                "Account-024 amount is too small to allocate a positive value to every Serial No"
            )
        parts.append((Decimal("1"), part_amount, serial_no))
        assigned += part_amount
    return tuple(parts)


def _post_entry(
    frappe: Any,
    *,
    reference_doctype: str,
    reference_name: str,
    reference_detail: str,
    direction: str,
    company: str,
    account: str,
    posting_date: Any,
    supplier: str,
    item_code: str,
    warehouse: str,
    batch_no: str | None,
    serial_no: str | None,
    uom: str,
    currency: str,
    qty: Decimal,
    amount: Decimal,
) -> Any:
    from erpnext_ua.ua_accounting.off_balance import create_off_balance_entry

    serial_key = serial_no or "all"
    key = (
        f"cc:{reference_doctype}:{reference_name}:{reference_detail}:"
        f"{direction.lower()}:{serial_key}"
    )
    return create_off_balance_entry(
        {
            "company": company,
            "posting_date": posting_date,
            "off_balance_account": account,
            "direction": direction,
            "quantity": qty,
            "uom": uom,
            "amount": amount,
            "currency": currency,
            "party_type": "Supplier",
            "party": supplier,
            "item_code": item_code,
            "warehouse": warehouse,
            "batch_no": batch_no,
            "serial_no": serial_no,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "reference_detail": reference_detail,
            "external_reference_key": key,
            "remarks": "ERPNext Consignment and Commission / UA account 024",
        },
        ignore_permissions=True,
    )


def _available_amount(frappe: Any, *, lot: Any, serial_no: str | None) -> tuple[Decimal, Decimal]:
    from erpnext_ua.ua_accounting.off_balance import get_available_balance

    probe = frappe._dict(
        name=None,
        company=lot.company,
        off_balance_account=lot.off_balance_account,
        party_type="Supplier",
        party=lot.supplier,
        item_code=lot.item_code,
        warehouse=lot.warehouse,
        batch_no=lot.batch_no,
        serial_no=serial_no,
        uom=lot.stock_uom,
        currency=lot.off_balance_currency,
    )
    qty, amount = get_available_balance(probe)
    return _decimal(qty), _decimal(amount)


def _lot_available(frappe: Any, lot: Any) -> tuple[Decimal, Decimal]:
    """Calculate the exact active balance of one immutable CC lot.

    Account 024 is legally analysed by principal and goods.  Multiple receipt
    lots can therefore share one statutory ledger balance, while this
    application still has to preserve each lot's own rounding residual.
    """
    frappe.db.sql(
        "select name from `tabCC Stock Lot` where name = %s for update",
        (lot.name,),
    )
    sales = frappe.db.sql(
        """
        select coalesce(sum(allocation.sold_qty), 0),
               coalesce(sum(allocation.off_balance_amount), 0)
        from `tabCC Sale Allocation` allocation
        inner join `tabSales Invoice` invoice
            on invoice.name = allocation.sales_invoice and invoice.docstatus = 1
        where allocation.stock_lot = %s
          and coalesce(allocation.off_balance_entry, '') != ''
        """,
        (lot.name,),
    )[0]
    returns = frappe.db.sql(
        """
        select coalesce(sum(audit.returned_qty), 0),
               coalesce(sum(audit.off_balance_amount), 0)
        from `tabCC Sale Return Allocation` audit
        inner join `tabSales Invoice` invoice
            on invoice.name = audit.return_sales_invoice and invoice.docstatus = 1
        where audit.stock_lot = %s
          and coalesce(audit.off_balance_entry, '') != ''
        """,
        (lot.name,),
    )[0]
    exits = frappe.db.sql(
        """
        select coalesce(sum(qty), 0), coalesce(sum(off_balance_amount), 0)
        from (
            select partner_return.qty, partner_return.off_balance_amount
            from `tabCC Partner Return` partner_return
            where partner_return.source_lot = %s
              and partner_return.docstatus = 1
              and coalesce(partner_return.off_balance_entry, '') != ''
            union all
            select conversion.qty, conversion.off_balance_amount
            from `tabCC Ownership Conversion` conversion
            where conversion.source_lot = %s
              and conversion.docstatus = 1
              and coalesce(conversion.off_balance_entry, '') != ''
        ) movements
        """,
        (lot.name, lot.name),
    )[0]
    quantity = (
        _decimal(lot.received_qty)
        - _decimal(sales[0])
        + _decimal(returns[0])
        - _decimal(exits[0])
    )
    amount = (
        _decimal(lot.off_balance_amount)
        - _decimal(sales[1])
        + _decimal(returns[1])
        - _decimal(exits[1])
    )
    return quantity, amount


def _decrease_lot(
    frappe: Any,
    *,
    document: Any,
    reference_detail: str,
    lot: Any,
    qty: Decimal,
    serial_numbers: tuple[str, ...],
) -> tuple[Decimal, tuple[Any, ...]]:
    desired = _rounded_amount(frappe, lot.off_balance_unit_value, qty)
    lot_qty, lot_amount = _lot_available(frappe, lot)
    if qty > lot_qty:
        frappe.throw(f"CC Stock Lot {lot.name} account-024 quantity is only {lot_qty}")
    if qty == lot_qty:
        desired = lot_amount
    if desired <= 0 or desired > lot_amount:
        frappe.throw(f"CC Stock Lot {lot.name} account-024 amount is only {lot_amount}")
    parts = _movement_parts(
        frappe,
        qty=qty,
        amount=desired,
        serial_numbers=serial_numbers,
    )
    entries = []
    total = Decimal("0")
    for part_qty, part_amount, serial_no in parts:
        available_qty, available_amount = _available_amount(
            frappe,
            lot=lot,
            serial_no=serial_no,
        )
        if part_qty > available_qty:
            frappe.throw(
                f"CC Stock Lot {lot.name} account-024 quantity is only {available_qty}"
            )
        if part_qty == available_qty:
            part_amount = available_amount
        if part_amount <= 0 or part_amount > available_amount:
            frappe.throw(
                f"CC Stock Lot {lot.name} account-024 amount is only {available_amount}"
            )
        detail = f"{reference_detail}:{serial_no}" if serial_no else reference_detail
        entries.append(
            _post_entry(
                frappe,
                reference_doctype=document.doctype,
                reference_name=document.name,
                reference_detail=detail,
                direction="Decrease",
                company=lot.company,
                account=lot.off_balance_account,
                posting_date=document.posting_date,
                supplier=lot.supplier,
                item_code=lot.item_code,
                warehouse=lot.warehouse,
                batch_no=lot.batch_no,
                serial_no=serial_no,
                uom=lot.stock_uom,
                currency=lot.off_balance_currency,
                qty=part_qty,
                amount=part_amount,
            )
        )
        total += part_amount
    return total, tuple(entries)


def post_receipt_off_balance(receipt: Any) -> tuple[str, ...]:
    """Increase account 024 for every accepted lot at acceptance-act value."""
    import frappe

    names = []
    for row in receipt.items:
        lot = frappe.get_doc("CC Stock Lot", row.stock_lot)
        qty = _decimal(row.stock_qty)
        amount = _decimal(row.accounting_amount)
        parts = _movement_parts(
            frappe,
            qty=qty,
            amount=amount,
            serial_numbers=_serials(row.serial_numbers),
        )
        entries = []
        for part_qty, part_amount, serial_no in parts:
            detail = f"{row.name}:{serial_no}" if serial_no else row.name
            entry = _post_entry(
                frappe,
                reference_doctype=receipt.doctype,
                reference_name=receipt.name,
                reference_detail=detail,
                direction="Increase",
                company=receipt.company,
                account=lot.off_balance_account,
                posting_date=receipt.posting_date,
                supplier=receipt.supplier,
                item_code=row.item_code,
                warehouse=receipt.warehouse,
                batch_no=row.batch_no,
                serial_no=serial_no,
                uom=row.stock_uom,
                currency=lot.off_balance_currency,
                qty=part_qty,
                amount=part_amount,
            )
            entries.append(entry)
            names.append(entry.name)
        primary = entries[0].name
        frappe.db.set_value(
            "CC Receipt Item",
            row.name,
            "off_balance_entry",
            primary,
            update_modified=False,
        )
        frappe.db.set_value(
            "CC Stock Lot",
            lot.name,
            "off_balance_entry",
            primary,
            update_modified=False,
        )
    return tuple(names)


def post_sale_off_balance(invoice: Any, allocations: list[Any]) -> None:
    """Decrease 024 when retained-ownership goods leave on a customer sale."""
    import frappe

    for allocation in allocations:
        if allocation.relationship_model == "OWN":
            continue
        lot = frappe.get_doc("CC Stock Lot", allocation.stock_lot)
        serials = (allocation.serial_no,) if allocation.serial_no else ()
        amount, entries = _decrease_lot(
            frappe,
            document=invoice,
            reference_detail=allocation.sales_invoice_item,
            lot=lot,
            qty=_decimal(allocation.sold_qty),
            serial_numbers=serials,
        )
        frappe.db.set_value(
            "CC Sale Allocation",
            allocation.name,
            {"off_balance_amount": amount, "off_balance_entry": entries[0].name},
            update_modified=False,
        )


def post_return_off_balance(invoice: Any, audits: list[Any]) -> None:
    """Restore the exact proportional 024 amount for a customer return."""
    import frappe

    for audit in audits:
        if audit.relationship_model == "OWN":
            continue
        sale = frappe.get_doc("CC Sale Allocation", audit.sale_allocation)
        lot = frappe.get_doc("CC Stock Lot", sale.stock_lot)
        prior = sum(
            (
                _decimal(row.off_balance_amount)
                for row in frappe.get_all(
                    "CC Sale Return Allocation",
                    filters={
                        "sale_allocation": sale.name,
                        "name": ("!=", audit.name),
                        "status": "RETURNED",
                    },
                    fields=["off_balance_amount"],
                )
            ),
            Decimal("0"),
        )
        remaining_amount = _decimal(sale.off_balance_amount) - prior
        remaining_qty = _decimal(sale.sold_qty) - (
            _decimal(sale.returned_qty) - _decimal(audit.returned_qty)
        )
        qty = _decimal(audit.returned_qty)
        amount = (
            remaining_amount
            if qty == remaining_qty
            else _rounded_amount(frappe, _decimal(sale.off_balance_amount) / _decimal(sale.sold_qty), qty)
        )
        serials = (sale.serial_no,) if sale.serial_no else ()
        parts = _movement_parts(
            frappe,
            qty=qty,
            amount=amount,
            serial_numbers=serials,
        )
        entries = []
        for part_qty, part_amount, serial_no in parts:
            detail = f"{audit.return_sales_invoice_item}:{serial_no}" if serial_no else audit.return_sales_invoice_item
            entries.append(
                _post_entry(
                    frappe,
                    reference_doctype=invoice.doctype,
                    reference_name=invoice.name,
                    reference_detail=detail,
                    direction="Increase",
                    company=lot.company,
                    account=lot.off_balance_account,
                    posting_date=invoice.posting_date,
                    supplier=lot.supplier,
                    item_code=lot.item_code,
                    warehouse=lot.warehouse,
                    batch_no=lot.batch_no,
                    serial_no=serial_no,
                    uom=lot.stock_uom,
                    currency=lot.off_balance_currency,
                    qty=part_qty,
                    amount=part_amount,
                )
            )
        frappe.db.set_value(
            "CC Sale Return Allocation",
            audit.name,
            {"off_balance_amount": amount, "off_balance_entry": entries[0].name},
            update_modified=False,
        )


def post_partner_exit_off_balance(document: Any) -> None:
    """Decrease 024 for a return to the partner or conversion to OWN."""
    import frappe

    lot = frappe.get_doc("CC Stock Lot", document.source_lot)
    amount, entries = _decrease_lot(
        frappe,
        document=document,
        reference_detail=document.source_lot,
        lot=lot,
        qty=_decimal(document.qty),
        serial_numbers=_serials(document.serial_numbers),
    )
    document.db_set(
        {"off_balance_amount": amount, "off_balance_entry": entries[0].name},
        update_modified=False,
    )
    document.off_balance_amount = amount
    document.off_balance_entry = entries[0].name


def cancel_reference_off_balance(document: Any) -> None:
    """Cancel every active simple-ledger movement produced by one document."""
    import frappe

    names = frappe.get_all(
        "UA Off Balance Entry",
        filters={
            "reference_doctype": document.doctype,
            "reference_name": document.name,
            "docstatus": 1,
        },
        pluck="name",
        order_by="creation desc",
    )
    for name in names:
        entry = frappe.get_doc("UA Off Balance Entry", name)
        ignored = set(entry.get("ignore_linked_doctypes") or ())
        ignored.add(document.doctype)
        entry.ignore_linked_doctypes = tuple(sorted(ignored))
        entry.cancel()
