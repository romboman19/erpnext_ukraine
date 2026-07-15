def after_install():
    from ukrainian_integrations.migrations import refresh_desk_navigation
    from ukrainian_integrations.pbx_sms.vitalpbx.custom_fields import ensure_integration_custom_fields

    ensure_integration_custom_fields()
    refresh_desk_navigation()
    return {"ok": True}
