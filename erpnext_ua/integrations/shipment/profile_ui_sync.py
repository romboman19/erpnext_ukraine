from __future__ import annotations

from pathlib import Path

import frappe

from erpnext_ua.integrations.utils.security import SYSTEM_ROLES, require_roles


def _read(rel: str) -> str:
    base = Path(__file__).resolve().parents[1]
    return (base / rel).read_text()


def _upsert(name: str, dt: str, view: str, rel: str):
    script = _read(rel)
    if frappe.db.exists("Client Script", name):
        doc = frappe.get_doc("Client Script", name)
        doc.dt = dt
        doc.view = view
        doc.enabled = 1
        doc.script = script
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            "doctype": "Client Script",
            "name": name,
            "dt": dt,
            "view": view,
            "enabled": 1,
            "script": script,
        })
        doc.insert(ignore_permissions=True)


@frappe.whitelist()
def sync_profile_ui_scripts():
    require_roles(*SYSTEM_ROLES)
    _upsert("UI NP Sender Profile Actions", "NP Sender Profile", "Form", "public/js/np_sender_profile_actions.js")
    _upsert("UI UP Sender Profile Actions", "UP Sender Profile", "Form", "public/js/up_sender_profile_actions.js")
    _upsert("UI NP Sender Profile List", "NP Sender Profile", "List", "public/js/np_sender_profile_list.js")
    _upsert("UI UP Sender Profile List", "UP Sender Profile", "List", "public/js/up_sender_profile_list.js")
    frappe.db.commit()
    return {"ok": True}
