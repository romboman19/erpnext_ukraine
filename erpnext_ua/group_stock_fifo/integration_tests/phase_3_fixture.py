"""Committed Phase 3 fixture: three FOP pools of one item at one location.

Shaped after §12.5/§37.1 — the seller owns the newest and largest layer, so any
run that fills its order from the seller's own pool has broken global FIFO.

Committed rather than rolled back because the double-booking check needs two
separate database connections to see the same stock. That makes teardown a real
obligation rather than a nicety, hence the confirmation tokens on both calls::

    docker exec frappe-test-backend-1 bench --site postest.local execute \
      erpnext_ua.group_stock_fifo.integration_tests.phase_3_fixture.build \
      --kwargs '{"confirm_write": "BUILD_GSF_PHASE_3"}'
"""

import frappe

ALLOWED_SITES = {"postest.local"}
GROUP = "GSF Phase 3"
LOCATION_CODE = "P3"
ITEM = "GSF-P3-ITEM"
CUSTOMER = "GSF Phase 3 Покупець"
LAYERS = [
    ("A", 2, 1000, "2026-01-10 09:00:00"),
    ("B", 3, 1100, "2026-02-01 09:00:00"),
    ("C", 5, 1200, "2026-03-15 09:00:00"),
]


def assert_site():
    if frappe.local.site not in ALLOWED_SITES:
        raise RuntimeError(f"Refusing to touch {frappe.local.site}")


def companies():
    return frappe.get_all("Company", pluck="name", order_by="name")[:3]


def pool_name(company):
    abbr = frappe.db.get_value("Company", company, "abbr")
    return f"GSF P3 Pool - {abbr}"


def stage_name(company):
    abbr = frappe.db.get_value("Company", company, "abbr")
    return f"GSF P3 Stage - {abbr}"


def clearing_account(company, side):
    """One balance-sheet account per company per side, as ADR-005 requires.

    Asset root on both sides: a receivable from the other FOP and a payable to
    it are both positions in the group, and §15.3 forbids anything that reaches
    profit and loss.
    """
    abbr = frappe.db.get_value("Company", company, "abbr")
    label = f"GSF Internal Stock Due {side}"
    name = f"{label} - {abbr}"
    if frappe.db.exists("Account", name):
        return name
    parent = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Current Assets", "is_group": 1}, "name"
    ) or frappe.db.get_value(
        "Account", {"company": company, "root_type": "Asset", "is_group": 1}, "name"
    )
    frappe.get_doc({
        "doctype": "Account", "account_name": label, "company": company,
        "parent_account": parent, "root_type": "Asset", "report_type": "Balance Sheet",
        "is_group": 0,
        "account_currency": frappe.db.get_value("Company", company, "default_currency"),
    }).insert(ignore_permissions=True)
    return name


def build(confirm_write):
    assert_site()
    if confirm_write != "BUILD_GSF_PHASE_3":
        raise RuntimeError("confirm_write token required")
    from erpnext_ua.group_stock_fifo.spikes.stock_setup import ensure_clearing_account

    firms = companies()
    currency = frappe.db.get_value("Company", firms[0], "default_currency")

    if not frappe.db.exists("Item", ITEM):
        frappe.get_doc({
            "doctype": "Item", "item_code": ITEM, "item_name": "GSF Phase 3 Item",
            "item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
            "stock_uom": "Nos", "is_stock_item": 1,
        }).insert(ignore_permissions=True)

    if not frappe.db.exists("GSF Company Group", GROUP):
        frappe.get_doc({
            "doctype": "GSF Company Group", "group_name": GROUP, "group_code": "P3",
            "enabled": 1, "base_currency": currency,
            "members": [
                {
                    "company": company, "enabled": 1, "can_source_stock": 1, "can_sell_stock": 1,
                    "default_due_from_stock_account": clearing_account(company, "From"),
                    "default_due_to_stock_account": clearing_account(company, "To"),
                }
                for company in firms
            ],
        }).insert(ignore_permissions=True)

    location = frappe.db.get_value("GSF Physical Location", {"location_code": LOCATION_CODE}, "name")
    if not location:
        location = frappe.get_doc({
            "doctype": "GSF Physical Location", "location_name": "GSF Phase 3 Location",
            "location_code": LOCATION_CODE, "company_group": GROUP,
        }).insert(ignore_permissions=True).name

    for company in firms:
        warehouse = pool_name(company)
        if not frappe.db.exists("Warehouse", warehouse):
            frappe.get_doc({
                "doctype": "Warehouse", "warehouse_name": "GSF P3 Pool", "company": company,
            }).insert(ignore_permissions=True)
        if not frappe.db.exists("GSF Warehouse Binding", warehouse):
            frappe.get_doc({
                "doctype": "GSF Warehouse Binding", "warehouse": warehouse, "company": company,
                "company_group": GROUP, "physical_location": location, "manager_app": "GSF",
                "warehouse_role": "GSF_OWN_POOL", "binding_mode": "MANAGED", "enabled": 1,
            }).insert(ignore_permissions=True)
        if not frappe.db.exists("GSF Location Company Binding",
                                {"company_group": GROUP, "company": company}):
            frappe.get_doc({
                "doctype": "GSF Location Company Binding", "company_group": GROUP,
                "physical_location": location, "company": company, "enabled": 1,
                "can_purchase": 1, "can_sell": 1, "own_pool_warehouse": warehouse,
            }).insert(ignore_permissions=True)

    seller = firms[2]
    stage = stage_name(seller)
    if not frappe.db.exists("Warehouse", stage):
        frappe.get_doc({
            "doctype": "Warehouse", "warehouse_name": "GSF P3 Stage", "company": seller,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("GSF Warehouse Binding", stage):
        frappe.get_doc({
            "doctype": "GSF Warehouse Binding", "warehouse": stage, "company": seller,
            "company_group": GROUP, "physical_location": location, "manager_app": "GSF",
            "warehouse_role": "GSF_SALE_STAGE", "binding_mode": "MANAGED", "enabled": 1,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("GSF Staging Lane", {"warehouse": stage}):
        frappe.get_doc({
            "doctype": "GSF Staging Lane", "lane_code": "P3-LANE-1", "company_group": GROUP,
            "physical_location": location, "company": seller, "warehouse": stage,
            "consumer_type": "MANUAL", "enabled": 1, "status": "AVAILABLE",
        }).insert(ignore_permissions=True)

    # §18.3: a managed sale needs one Item on several rows. Enabled here
    # explicitly — GSF itself must never flip a global ERPNext setting (§44) —
    # and restored by teardown.
    if not frappe.db.get_single_value("Selling Settings", "allow_multiple_items"):
        frappe.db.set_single_value("Selling Settings", "allow_multiple_items", 1)

    if not frappe.db.exists("Customer", CUSTOMER):
        frappe.get_doc({
            "doctype": "Customer", "customer_name": CUSTOMER, "customer_type": "Individual",
        }).insert(ignore_permissions=True)

    settings = frappe.get_single("GSF Settings")
    if not settings.enabled:
        settings.enabled = 1
        settings.save(ignore_permissions=True)

    receipts = []
    for (label, qty, rate, posting), company in zip(LAYERS, firms):
        warehouse = pool_name(company)
        if frappe.db.exists("Stock Entry", {"remarks": f"GSF-P3-{label}", "docstatus": 1}):
            continue
        entry = frappe.get_doc({
            "doctype": "Stock Entry", "stock_entry_type": "Material Receipt",
            "purpose": "Material Receipt", "company": company, "set_posting_time": 1,
            "posting_date": posting.split(" ")[0], "posting_time": posting.split(" ")[1],
            "gsf_managed": 1, "remarks": f"GSF-P3-{label}",
            "items": [{
                "item_code": ITEM, "qty": qty, "t_warehouse": warehouse,
                "basic_rate": rate, "set_basic_rate_manually": 1,
                "expense_account": ensure_clearing_account(frappe, company),
            }],
        })
        entry.insert(ignore_permissions=True)
        entry.submit()
        receipts.append(entry.name)

    drained = drain_reposts()
    frappe.db.commit()
    return {
        "location": location,
        "reposts_drained": drained,
        "companies": firms,
        "pools": [pool_name(company) for company in firms],
        "receipts": receipts,
        "layers": frappe.get_all("GSF Stock Layer", filters={"item_code": ITEM},
                                 fields=["name", "origin_company", "original_received_datetime",
                                         "original_received_qty", "layer_status"],
                                 order_by="original_received_datetime"),
    }


def drain_reposts():
    """Run the reposts a scheduler would have run.

    Backdated receipts make ERPNext queue a `Repost Item Valuation`, and this
    stack has no scheduler container, so those stay `Queued` forever. §17.2
    treats a pending repost as "the valuation queue is not settled" and refuses
    to issue against it — correctly, because until the repost runs the queue on
    disk is not what ERPNext will actually consume. Draining here is what a real
    stack does within seconds; it is not a workaround for the check.
    """
    from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import repost

    done = []
    for name in frappe.get_all(
        "Repost Item Valuation",
        filters={"status": ("in", ("Queued", "In Progress")), "docstatus": 1},
        order_by="posting_date asc, creation asc",
        pluck="name",
    ):
        try:
            repost(frappe.get_doc("Repost Item Valuation", name))
            done.append(name)
        except Exception:
            frappe.db.rollback()
            frappe.db.set_value("Repost Item Valuation", name, "status", "Failed")
            frappe.db.commit()
    return len(done)


def teardown(confirm_write):
    assert_site()
    if confirm_write != "DROP_GSF_PHASE_3":
        raise RuntimeError("confirm_write token required")
    frappe.flags.in_uninstall = True
    try:
        # Documents come apart in the reverse of the order they were built, and
        # the sale is the newest of them. Cancelling a stage receipt while the
        # invoice that consumed it is still submitted asks ERPNext for stock
        # that is no longer there, and it refuses with NegativeStockError —
        # correctly.
        for invoice in frappe.get_all(
            "Sales Invoice", filters={"customer": CUSTOMER}, fields=["name", "docstatus"],
            order_by="creation desc",
        ):
            doc = frappe.get_doc("Sales Invoice", invoice.name)
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc("Sales Invoice", invoice.name, force=True, ignore_permissions=True)

        for name in frappe.get_all("GSF Checkout",
                                   filters={"company_group": GROUP}, pluck="name"):
            frappe.delete_doc("GSF Checkout", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("GSF Stock Reallocation",
                                   filters={"company_group": GROUP}, pluck="name"):
            frappe.delete_doc("GSF Stock Reallocation", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("GSF Allocation", filters={"item_code": ITEM}, pluck="name"):
            frappe.delete_doc("GSF Allocation", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("GSF Layer Movement", pluck="name"):
            if frappe.db.get_value("GSF Stock Layer", frappe.db.get_value(
                    "GSF Layer Movement", name, "stock_layer"), "item_code") == ITEM:
                frappe.delete_doc("GSF Layer Movement", name, force=True, ignore_permissions=True)
        # Balances go before the vouchers. Cancelling a reallocation does not
        # reverse the §9.10 caches — Phase 4 has no compensation path yet — so
        # §11.4's guard would otherwise refuse to cancel the seeding receipt on
        # the grounds that its layer still holds stock in the stage.
        for name in frappe.get_all("GSF Layer Balance", pluck="name"):
            if frappe.db.get_value(
                "GSF Stock Layer", frappe.db.get_value("GSF Layer Balance", name, "stock_layer"),
                "item_code",
            ) == ITEM:
                frappe.delete_doc("GSF Layer Balance", name, force=True, ignore_permissions=True)

        # Cancelled newest-first: a reallocation receipt has to go before the
        # issue that funded it, and both before the receipt that seeded them.
        for entry in frappe.get_all(
            "Stock Entry",
            filters={"remarks": ("like", "%GSF%")},
            fields=["name", "docstatus"],
            order_by="creation desc",
        ):
            doc = frappe.get_doc("Stock Entry", entry.name)
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc("Stock Entry", entry.name, force=True, ignore_permissions=True)
        for layer in frappe.get_all("GSF Stock Layer", filters={"item_code": ITEM}, pluck="name"):
            for balance in frappe.get_all("GSF Layer Balance", filters={"stock_layer": layer}, pluck="name"):
                frappe.delete_doc("GSF Layer Balance", balance, force=True, ignore_permissions=True)
            frappe.delete_doc("GSF Stock Layer", layer, force=True, ignore_permissions=True)
        for name in frappe.get_all("GSF Scope Lock", filters={"item_code": ITEM}, pluck="name"):
            frappe.delete_doc("GSF Scope Lock", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("GSF Location Company Binding",
                                   filters={"company_group": GROUP}, pluck="name"):
            frappe.delete_doc("GSF Location Company Binding", name, force=True, ignore_permissions=True)
        for name in frappe.get_all("GSF Staging Lane", filters={"company_group": GROUP}, pluck="name"):
            frappe.delete_doc("GSF Staging Lane", name, force=True, ignore_permissions=True)
        for company in companies():
            for warehouse in (pool_name(company), stage_name(company)):
                if frappe.db.exists("GSF Warehouse Binding", warehouse):
                    frappe.delete_doc(
                        "GSF Warehouse Binding", warehouse, force=True, ignore_permissions=True
                    )
                frappe.db.sql("delete from `tabStock Ledger Entry` where warehouse = %s", (warehouse,))
                frappe.db.sql("delete from `tabBin` where warehouse = %s", (warehouse,))
                if frappe.db.exists("Warehouse", warehouse):
                    frappe.delete_doc("Warehouse", warehouse, force=True, ignore_permissions=True)
        location = frappe.db.get_value("GSF Physical Location", {"location_code": LOCATION_CODE}, "name")
        if location:
            frappe.delete_doc("GSF Physical Location", location, force=True, ignore_permissions=True)
        if frappe.db.exists("GSF Company Group", GROUP):
            frappe.delete_doc("GSF Company Group", GROUP, force=True, ignore_permissions=True)
        if frappe.db.exists("Item", ITEM):
            frappe.delete_doc("Item", ITEM, force=True, ignore_permissions=True)
        if frappe.db.exists("Customer", CUSTOMER):
            frappe.delete_doc("Customer", CUSTOMER, force=True, ignore_permissions=True)
        frappe.db.set_single_value("Selling Settings", "allow_multiple_items", 0)
        settings = frappe.get_single("GSF Settings")
        settings.enabled = 0
        settings.save(ignore_permissions=True)
    finally:
        frappe.flags.in_uninstall = False
    frappe.db.commit()
    return {
        "layers": frappe.db.count("GSF Stock Layer"),
        "allocations": frappe.db.count("GSF Allocation"),
        "balances": frappe.db.count("GSF Layer Balance"),
        "scope_locks": frappe.db.count("GSF Scope Lock"),
        "reallocations": frappe.db.count("GSF Stock Reallocation"),
        "checkouts": frappe.db.count("GSF Checkout"),
        "gsf_enabled": frappe.db.get_single_value("GSF Settings", "enabled"),
    }
