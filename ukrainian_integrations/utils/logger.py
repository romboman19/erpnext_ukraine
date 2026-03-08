import frappe

def log_event(level: str, message: str, context: dict | None = None):
    frappe.logger("ukrainian_integrations").info({
        "level": level,
        "message": message,
        "context": context or {},
    })
