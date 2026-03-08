from __future__ import annotations

import json
import frappe


def log_event(
    integration: str,
    status: str,
    message: str = "",
    *,
    direction: str = "out",
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    request_payload: dict | list | str | None = None,
    response_payload: dict | list | str | None = None,
    error_trace: str | None = None,
):
    def _dump(v):
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    try:
        doc = frappe.get_doc(
            {
                "doctype": "Hunter Integration Log",
                "integration": integration,
                "direction": direction,
                "status": status,
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "message": message,
                "request_payload": _dump(request_payload),
                "response_payload": _dump(response_payload),
                "error_trace": error_trace or "",
            }
        )
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.logger("ukrainian_integrations").error(
            {"integration": integration, "status": status, "message": message, "trace": frappe.get_traceback()}
        )
