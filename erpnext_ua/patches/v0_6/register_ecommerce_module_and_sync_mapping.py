from __future__ import annotations

import frappe

APP_NAME = "erpnext_ua"
MODULE = "Ecommerce"
DOCTYPE = "Ecommerce Item Mapping"


def execute() -> None:
    """Register the new module and sync the moved mapping DocType on upgrades.

    Frappe caches the app-to-module map.  Sites upgraded from a release whose
    ``modules.txt`` did not contain ``Ecommerce`` can therefore skip the new
    module during the model-sync phase of the same migrate run.
    """
    _ensure_module_def()
    _refresh_module_map()

    if frappe.db.exists("DocType", DOCTYPE):
        frappe.db.set_value(
            "DocType",
            DOCTYPE,
            "module",
            MODULE,
            update_modified=False,
        )

    # Sync this moved DocType explicitly.  The normal model sync that follows
    # will handle every other DocType in the newly registered module.
    frappe.reload_doc("ecommerce", "doctype", "ecommerce_item_mapping", force=True)
    frappe.clear_cache(doctype=DOCTYPE)


def _ensure_module_def() -> None:
    owner = frappe.db.get_value("Module Def", MODULE, "app_name")
    if owner and owner != APP_NAME:
        raise RuntimeError(f"Module {MODULE} is already owned by app {owner}")
    if owner == APP_NAME:
        return

    module = frappe.new_doc("Module Def")
    module.module_name = MODULE
    module.app_name = APP_NAME
    module.insert(ignore_permissions=True, ignore_if_duplicate=True)

    owner = frappe.db.get_value("Module Def", MODULE, "app_name")
    if owner != APP_NAME:
        raise RuntimeError(f"Could not register module {MODULE} for app {APP_NAME}")


def _refresh_module_map() -> None:
    frappe.cache.delete_value("app_modules")
    frappe.client_cache.delete_value("installed_app_modules")
    frappe.setup_module_map()
