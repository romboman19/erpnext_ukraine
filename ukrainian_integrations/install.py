def after_install():
    from ukrainian_integrations.pbx_sms.vitalpbx.custom_fields import ensure_integration_custom_fields

    ensure_integration_custom_fields()
    return {"ok": True}
