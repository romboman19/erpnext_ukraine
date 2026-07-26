frappe.ui.form.on('Sales Invoice', {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button('📞 VitalPBX: Подзвонити клієнту', async () => {
      let defaultExt = '';
      try {
        const ex = await frappe.call({ method: 'ukrainian_integrations.pbx_sms.vitalpbx.service.get_default_extension' });
        defaultExt = (ex.message && ex.message.extension) || '';
      } catch (_) {}
      const d = new frappe.ui.Dialog({
        title: 'Click-to-call (Sales Invoice)',
        fields: [
          { fieldname: 'extension', label: 'Extension', fieldtype: 'Data', reqd: 1, default: defaultExt, hidden: !!defaultExt },
          { fieldname: 'phone', label: 'Phone', fieldtype: 'Data', reqd: 1, default: frm.doc.contact_mobile || frm.doc.contact_phone || '' }
        ],
        primary_action_label: 'Подзвонити',
        primary_action: async (v) => {
          await frappe.call({
            method: 'ukrainian_integrations.pbx_sms.vitalpbx.service.click_to_call',
            args: {
              extension: v.extension,
              destination: v.phone,
              idempotency_key:
                typeof crypto !== 'undefined' && crypto.randomUUID
                  ? crypto.randomUUID()
                  : frappe.utils.get_random(32)
            }
          });
          frappe.show_alert({message: 'Дзвінок ініційовано', indicator: 'green'});
          d.hide();
        }
      });
      d.show();
    });
  }
});
