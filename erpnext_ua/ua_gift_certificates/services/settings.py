import frappe

from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError


def settings():
    return frappe.get_single("UA Gift Certificate Settings")


def require_enabled(*, pos_sale: bool = False, pos_redemption: bool = False):
    config = settings()
    if not config.enabled:
        raise GiftCertificateError("Gift Certificates are disabled", "CERT_MODULE_DISABLED")
    if pos_sale and not config.pos_sale_enabled:
        raise GiftCertificateError("POS certificate sale is disabled", "CERT_MODULE_DISABLED")
    if pos_redemption and not config.pos_redemption_enabled:
        raise GiftCertificateError("POS certificate redemption is disabled", "CERT_MODULE_DISABLED")
    return config


def enabled_for_pos_redemption() -> bool:
    if not frappe.db.exists("DocType", "UA Gift Certificate Settings"):
        return False
    values = dict(
        frappe.db.sql(
            """select field, value
               from `tabSingles`
               where doctype = 'UA Gift Certificate Settings'
                 and field in ('enabled', 'pos_redemption_enabled')"""
        )
    )
    return bool(
        frappe.utils.cint(values.get("enabled"))
        and frappe.utils.cint(values.get("pos_redemption_enabled"))
    )
