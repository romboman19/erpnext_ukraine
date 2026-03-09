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
          { fieldname: 'settlement_query', label: 'Пошук міста', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_settlement_ref', label: 'Settlement Ref', fieldtype: 'Data', read_only: 1, reqd: 1, hidden: 1 },
          { fieldname: 'recipient_city_ref', label: 'City Ref', fieldtype: 'Data', read_only: 1, reqd: 1, hidden: 1 },
          { fieldname: 'warehouse_query', label: 'Пошук відділення/поштомата', fieldtype: 'Data' },
          { fieldname: 'selected_warehouse', label: 'Обране відділення', fieldtype: 'Data', read_only: 1 },
          { fieldname: 'recipient_warehouse_ref', label: 'Warehouse Ref', fieldtype: 'Data', reqd: 1, hidden: 1 },
          { fieldname: 'description', label: 'Опис', fieldtype: 'Data' },
          { fieldname: 'declared_cost', label: 'Оголошена вартість', fieldtype: 'Currency', default: 100 },
          { fieldname: 'weight', label: 'Вага, кг', fieldtype: 'Float', default: 1 },
          { fieldname: 'seats_amount', label: 'К-сть місць', fieldtype: 'Int', default: 1 }
        ],
        primary_action_label: 'Створити',
        primary_action: async (v) => {
          const x = await frappe.call({ method: 'ukrainian_integrations.shipment.nova_poshta.service.create_ttn_standalone', args: v });
          const m=(x.message||{}); const stickerUrl=m.sticker_url || (m.ttn_ref ? `https://my.novaposhta.ua/orders/printMarking100x100/orders[]/${m.ttn_ref}` : (m.print_url||'')); frappe.msgprint('ТТН: ' + (m.ttn_number || '-') + (stickerUrl ? `<br><a href=\"${stickerUrl}\" target=\"_blank\">Друк стікера 11x11</a>` : ''));
          d.hide();
        }
      });

      const sq = d.get_field('settlement_query');
      sq.$input.on('change', async () => {
        const q = (d.get_value('settlement_query') || '').trim();
        if (!q) return;
        const r2 = await frappe.call({
          method: 'ukrainian_integrations.shipment.nova_poshta.service.np_search_settlements',
          args: { query: q, sender_profile: d.get_value('sender_profile') }
        });
        const items = (r2.message && r2.message.items) || [];
        if (!items.length) return;
        d.set_value('recipient_settlement_ref', items[0].settlement_ref || '');
        d.set_value('recipient_city_ref', items[0].city_ref || '');
      });

      const wq = d.get_field('warehouse_query');
      wq.$input.on('change', async () => {
        const settlement_ref = (d.get_value('recipient_settlement_ref') || '').trim();
        const query = (d.get_value('warehouse_query') || '').trim();
        if (!settlement_ref) return;
        const r3 = await frappe.call({
          method: 'ukrainian_integrations.shipment.nova_poshta.service.np_search_warehouses',
          args: { settlement_ref, query, sender_profile: d.get_value('sender_profile') }
        });
        const items = (r3.message && r3.message.items) || [];
        if (!items.length) return;
        d.set_value('recipient_warehouse_ref', items[0].ref || '');
        d.set_value('selected_warehouse', items[0].label || items[0].short || '');
      });

      d.show();
    });
  }
};
