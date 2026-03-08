frappe.ui.form.on('Customer', {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button('📞 VitalPBX: Подзвонити', async () => {
      const d = new frappe.ui.Dialog({
        title: 'Click-to-call',
        fields: [
          { fieldname: 'extension', label: 'Extension', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'phone', label: 'Phone', fieldtype: 'Data', reqd: 1, default: frm.doc.mobile_no || frm.doc.phone || '' }
        ],
        primary_action_label: 'Подзвонити',
        primary_action: async (v) => {
          await frappe.call({
            method: 'ukrainian_integrations.pbx_sms.vitalpbx.service.click_to_call',
            args: { extension: v.extension, destination: v.phone }
          });
          frappe.show_alert({message: 'Дзвінок ініційовано', indicator: 'green'});
          d.hide();
        }
      });
      d.show();
    });
  }
});
