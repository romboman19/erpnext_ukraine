frappe.ui.form.on('NP Sender Profile', {
  refresh(frm) {
    frm.add_custom_button('Створити ТТН (довільно)', async () => {
      const d = new frappe.ui.Dialog({
        title: 'НП ТТН із профілю',
        fields: [
                    {fieldname:'recipient_name', label:'Одержувач', fieldtype:'Data', reqd:1},
          {fieldname:'recipient_phone', label:'Телефон', fieldtype:'Data', reqd:1},
          {fieldname:'recipient_settlement_ref', label:'Settlement Ref', fieldtype:'Data', reqd:1},
          {fieldname:'recipient_city_ref', label:'City Ref', fieldtype:'Data', reqd:1},
          {fieldname:'recipient_warehouse_ref', label:'Warehouse Ref', fieldtype:'Data', reqd:1},
        ],
        primary_action_label: 'Створити',
        primary_action: async (v) => {
          const r = await frappe.call({
            method: 'ukrainian_integrations.shipment.nova_poshta.service.create_ttn_standalone',
            args: {...v, sender_profile: frm.doc.profile_name || frm.doc.name}
          });
          frappe.msgprint('ТТН: ' + ((r.message||{}).ttn_number || '-'));
          d.hide();
        }
      });
      d.show();
    });
  }
});
