frappe.ui.form.on('NP Sender Profile', {
  refresh(frm) {
    frm.add_custom_button('Створити ТТН (довільно)', async () => {
      const d = new frappe.ui.Dialog({
        title: 'НП ТТН із профілю',
        fields: [
          { fieldname: 'recipient_name', label: 'Одержувач', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_phone', label: 'Телефон', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'settlement_query', label: 'Пошук міста', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_settlement_ref', label: 'Settlement Ref', fieldtype: 'Data', read_only: 1, reqd: 1 },
          { fieldname: 'recipient_city_ref', label: 'City Ref', fieldtype: 'Data', read_only: 1, reqd: 1 },
          { fieldname: 'warehouse_query', label: 'Пошук відділення/поштомата', fieldtype: 'Data' },
          { fieldname: 'recipient_warehouse_ref', label: 'Warehouse Ref', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'description', label: 'Опис', fieldtype: 'Data' },
          { fieldname: 'declared_cost', label: 'Оголошена вартість', fieldtype: 'Currency', default: 100 },
          { fieldname: 'weight', label: 'Вага, кг', fieldtype: 'Float', default: 1 },
          { fieldname: 'seats_amount', label: 'К-сть місць', fieldtype: 'Int', default: 1 },
        ],
        primary_action_label: 'Створити',
        primary_action: async (v) => {
          const r = await frappe.call({
            method: 'ukrainian_integrations.shipment.nova_poshta.service.create_ttn_standalone',
            args: { ...v, sender_profile: frm.doc.profile_name || frm.doc.name }
          });
          frappe.msgprint('ТТН: ' + ((r.message || {}).ttn_number || '-'));
          d.hide();
        }
      });

      const sq = d.get_field('settlement_query');
      sq.$input.on('change', async () => {
        const q = (d.get_value('settlement_query') || '').trim();
        if (!q) return;
        const r = await frappe.call({
          method: 'ukrainian_integrations.shipment.nova_poshta.service.np_search_settlements',
          args: { query: q, sender_profile: frm.doc.profile_name || frm.doc.name }
        });
        const items = (r.message && r.message.items) || [];
        if (!items.length) return;
        const first = items[0];
        d.set_value('recipient_settlement_ref', first.settlement_ref || '');
        d.set_value('recipient_city_ref', first.city_ref || '');
      });

      const wq = d.get_field('warehouse_query');
      wq.$input.on('change', async () => {
        const settlement_ref = (d.get_value('recipient_settlement_ref') || '').trim();
        const query = (d.get_value('warehouse_query') || '').trim();
        if (!settlement_ref) return;
        const r = await frappe.call({
          method: 'ukrainian_integrations.shipment.nova_poshta.service.np_search_warehouses',
          args: { settlement_ref, query, sender_profile: frm.doc.profile_name || frm.doc.name }
        });
        const items = (r.message && r.message.items) || [];
        if (!items.length) return;
        d.set_value('recipient_warehouse_ref', items[0].ref || '');
      });

      d.show();
    });
  }
});
