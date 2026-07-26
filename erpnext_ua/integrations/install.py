def after_install():
    from erpnext_ua.integrations.communication.telegram.customizations import (
        ensure_telegram_customizations,
    )
    from erpnext_ua.integrations.migrations import (
        ensure_identification_channel_defaults,
        refresh_desk_navigation,
    )
    from erpnext_ua.integrations.pbx_sms.vitalpbx.custom_fields import ensure_integration_custom_fields

    ensure_integration_custom_fields()
    ensure_identification_channel_defaults()
    ensure_telegram_customizations()
    refresh_desk_navigation()
    return {"ok": True}
