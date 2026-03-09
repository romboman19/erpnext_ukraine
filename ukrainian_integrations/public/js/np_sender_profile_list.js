frappe.listview_settings['NP Sender Profile'] = {
  onload(listview) {
    listview.page.add_menu_item('Створити ТТН (довільно)', async () => {
      const r = await frappe.call({ method: 'ukrainian_integrations.shipment.nova_poshta.service.np_sender_profiles_list' });
      const opts = ((r.message && r.message.items) || []).map(x => x.name);
      const d = new frappe.ui.Dialog({
        title: 'НП ТТН з переліку профілів',
        fields: [
          { fieldname: 'sender_profile', label: 'Профіль', fieldtype: 'Select', options: opts.join('\n'), reqd: 1 },
          { fieldname: 'recipient_name', label: 'Одержувач', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_phone', label: 'Телефон', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_settlement_ref', label: 'Settlement Ref', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_city_ref', label: 'City Ref', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_warehouse_ref', label: 'Warehouse Ref', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'description', label: 'Опис', fieldtype: 'Data' },
          { fieldname: 'declared_cost', label: 'Оголошена вартість', fieldtype: 'Currency', default: 100 },
          { fieldname: 'weight', label: 'Вага, кг', fieldtype: 'Float', default: 1 },
          { fieldname: 'seats_amount', label: 'К-сть місць', fieldtype: 'Int', default: 1 }
        ],
        primary_action_label: 'Створити',
        primary_action: async (v) => {
          const x = await frappe.call({
            method: 'ukrainian_integrations.shipment.nova_poshta.service.create_ttn_standalone',
            args: v
          });
          frappe.msgprint('ТТН: ' + ((x.message || {}).ttn_number || '-'));
          d.hide();
        }
      });
      d.show();
    });
  }
};
