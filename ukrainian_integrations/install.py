def after_install():
    from ukrainian_integrations.migrations import (
        ensure_identification_channel_defaults,
        refresh_desk_navigation,
    )
    from ukrainian_integrations.pbx_sms.vitalpbx.custom_fields import ensure_integration_custom_fields

    ensure_integration_custom_fields()
    ensure_identification_channel_defaults()
    refresh_desk_navigation()
    return {"ok": True}
