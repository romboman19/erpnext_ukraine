from contextlib import contextmanager

import frappe

from .constants import WRITE_FLAG


@contextmanager
def service_write():
    previous = getattr(frappe.flags, WRITE_FLAG, False)
    setattr(frappe.flags, WRITE_FLAG, True)
    try:
        yield
    finally:
        setattr(frappe.flags, WRITE_FLAG, previous)
