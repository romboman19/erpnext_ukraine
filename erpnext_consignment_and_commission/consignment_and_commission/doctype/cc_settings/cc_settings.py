import frappe
from frappe.model.document import Document

from ...services.foundation import FoundationValidationError, SettingsPolicy, validate_settings_policy


class CCSettings(Document):
    def validate(self) -> None:
        try:
            validate_settings_policy(
                SettingsPolicy(
                    enable_commission=bool(self.enable_commission),
                    enable_consignment=bool(self.enable_consignment),
                    reservation_ttl_minutes=int(self.reservation_ttl_minutes or 0),
                    allocation_retry_limit=int(self.allocation_retry_limit or 0),
                    enable_buyout=bool(self.enable_buyout),
                    enable_deferred_purchase=bool(self.enable_deferred_purchase),
                )
            )
        except FoundationValidationError as exc:
            frappe.throw(str(exc))

        if self.enabled:
            missing = [
                doctype
                for doctype in ("Buying Settings", "Selling Settings")
                if not frappe.db.get_single_value(doctype, "allow_multiple_items")
            ]
            if missing:
                frappe.throw(
                    "CC operations require Allow Item to be Added Multiple Times in a Transaction in "
                    + " and ".join(missing)
                    + ". Separate rows preserve exact CC Stock Lot identity."
                )

        if not self.default_location:
            return
        location = frappe.db.get_value(
            "CC Location",
            self.default_location,
            ["company", "disabled"],
            as_dict=True,
        )
        if not location or location.disabled:
            frappe.throw("Default CC Location must exist and be enabled")
        if self.default_company and location.company != self.default_company:
            frappe.throw("Default CC Location must belong to the default Company")
