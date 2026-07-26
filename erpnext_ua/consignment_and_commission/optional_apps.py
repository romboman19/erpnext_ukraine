from collections.abc import Iterable

from .constants import OPTIONAL_APPS


def installed_optional_apps(installed_apps: Iterable[str] | None = None) -> frozenset[str]:
    """Return supported optional apps that are installed on the current site."""
    if installed_apps is None:
        import frappe

        installed_apps = frappe.get_installed_apps()

    return frozenset(installed_apps).intersection(OPTIONAL_APPS)


def is_app_installed(app_name: str, installed_apps: Iterable[str] | None = None) -> bool:
    if installed_apps is None:
        import frappe

        installed_apps = frappe.get_installed_apps()

    return app_name in frozenset(installed_apps)
