from __future__ import annotations

import frappe

from erpnext_ua.ua_gift_certificates.domain.errors import GiftCertificateError

from .issuance import issue_certificate


def generate_batch(batch_name: str, *, commit_progress: bool = True) -> dict:
    frappe.db.sql("select name from `tabUA Gift Certificate Batch` where name=%s for update", batch_name)
    batch = frappe.get_doc("UA Gift Certificate Batch", batch_name)
    if batch.status in {"Cancelled", "Closed"}:
        raise GiftCertificateError("Batch is closed", "CERT_BATCH_CLOSED")
    program = frappe.get_doc("UA Gift Certificate Program", batch.program)
    entity = _issuer_entity(program.network)
    batch.db_set("status", "Generating", update_modified=False)
    generated = 0
    for sequence in range(1, int(batch.quantity) + 1):
        issue_certificate(
            program_name=program.name,
            face_value=batch.face_value,
            sale_price=batch.sale_price or batch.face_value,
            holder_mode=batch.holder_mode,
            issuer_company=entity.company,
            issuer_fop_profile=entity.fop_profile,
            batch=batch.name,
            idempotency_key=f"issue:{batch.name}:{sequence}",
        )
        generated += 1
        if commit_progress and sequence % 100 == 0:
            batch.db_set("generated_count", generated, update_modified=False)
            frappe.db.commit()
    batch.db_set(
        {"generated_count": generated, "status": "Issued"},
        update_modified=False,
    )
    if commit_progress:
        frappe.db.commit()
    return {"batch": batch.name, "status": "Issued", "generated_count": generated}


def queue_batch(batch_name: str) -> dict:
    batch = frappe.get_doc("UA Gift Certificate Batch", batch_name)
    if batch.status not in {"Draft", "Failed", "Generating"}:
        return {"batch": batch.name, "status": batch.status, "generated_count": batch.generated_count}
    batch.db_set("status", "Generating", update_modified=False)
    frappe.enqueue(
        "erpnext_ua.ua_gift_certificates.services.batch.generate_batch",
        queue="long",
        enqueue_after_commit=True,
        job_name=f"gift-certificate-batch-{batch.name}",
        batch_name=batch.name,
    )
    frappe.db.commit()
    return {"batch": batch.name, "status": "Generating"}


def _issuer_entity(network: str):
    rows = frappe.get_all(
        "UA Gift Certificate Network Entity",
        filters={"parent": network, "entity_role": ("in", ["Issuer", "Both"])},
        fields=["company", "fop_profile"],
        limit=2,
    )
    if len(rows) != 1:
        raise GiftCertificateError(
            "Batch issuance requires exactly one issuer entity",
            "CERT_COMPLIANCE_DENIED",
        )
    return rows[0]
