frappe.ui.form.on('NP Sender Profile', {
  refresh(frm) {
    frm.add_custom_button('Створити ТТН (довільно)', async () => {
      const d = new frappe.ui.Dialog({
        title: 'НП ТТН із профілю (розширено)',
        fields: [
          { fieldname: 'recipient_name', label: 'Одержувач', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_phone', label: 'Телефон', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'settlement_query', label: 'Пошук міста', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_settlement_ref', label: 'Settlement Ref', fieldtype: 'Data', read_only: 1, reqd: 1 },
          { fieldname: 'recipient_city_ref', label: 'City Ref', fieldtype: 'Data', read_only: 1, reqd: 1 },
          { fieldname: 'warehouse_query', label: 'Пошук відділення/поштомата', fieldtype: 'Data' },
          { fieldname: 'recipient_warehouse_ref', label: 'Warehouse Ref', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'section_break_1', fieldtype: 'Section Break', label: 'Параметри відправлення' },
          { fieldname: 'cargo_type', label: 'Тип вантажу', fieldtype: 'Select', options: 'Parcel\nCargo\nDocuments\nTiresWheels', default: 'Parcel' },
          { fieldname: 'service_type', label: 'Тип доставки', fieldtype: 'Select', options: 'WarehouseWarehouse\nDoorsWarehouse\nWarehouseDoors\nDoorsDoors', default: 'WarehouseWarehouse' },
          { fieldname: 'payer_type', label: 'Платник', fieldtype: 'Select', options: 'Sender\nRecipient\nThirdPerson', default: 'Recipient' },
          { fieldname: 'payment_method', label: 'Спосіб оплати', fieldtype: 'Select', options: 'Cash\nNonCash', default: 'Cash' },
          { fieldname: 'weight', label: 'Вага, кг', fieldtype: 'Float', default: 0.5 },
          { fieldname: 'seats_amount', label: 'К-сть місць', fieldtype: 'Int', default: 1 },
          { fieldname: 'width', label: 'Ширина, см', fieldtype: 'Float', default: 15 },
          { fieldname: 'length', label: 'Довжина, см', fieldtype: 'Float', default: 10 },
          { fieldname: 'height', label: 'Висота, см', fieldtype: 'Float', default: 5 },
          { fieldname: 'declared_cost', label: 'Оголошена вартість', fieldtype: 'Currency', default: 100 },
          { fieldname: 'cod_amount', label: 'Зворотна доставка (грн)', fieldtype: 'Currency' },
          { fieldname: 'return_delivery_type', label: 'Тип зворотної доставки', fieldtype: 'Select', options: 'Money\nGoods', default: 'Money' },
          { fieldname: 'order_no', label: 'Номер замовлення', fieldtype: 'Data' },
          { fieldname: 'description', label: 'Опис вкладення', fieldtype: 'Data' }
        ],
        size: 'extra-large',
        primary_action_label: 'Створити',
        primary_action: async (v) => {
          const r = await frappe.call({ method: 'ukrainian_integrations.shipment.nova_poshta.service.create_ttn_standalone', args: { ...v, sender_profile: frm.doc.profile_name || frm.doc.name } });
          const m = (r.message || {});
          frappe.msgprint(`ТТН: ${m.ttn_number || '-'}${m.print_url ? `<br><a href="${m.print_url}" target="_blank">Друк ТТН</a>` : ''}`);
          d.hide();
        }
      });

      d.get_field('settlement_query').$input.on('change', async () => {
        const q = (d.get_value('settlement_query') || '').trim(); if (!q) return;
        const r = await frappe.call({ method:'ukrainian_integrations.shipment.nova_poshta.service.np_search_settlements', args:{ query:q, sender_profile: frm.doc.profile_name || frm.doc.name }});
        const items = (r.message && r.message.items) || []; if (!items.length) return;
        d.set_value('recipient_settlement_ref', items[0].settlement_ref || '');
        d.set_value('recipient_city_ref', items[0].city_ref || '');
      });

      d.get_field('warehouse_query').$input.on('change', async () => {
        const settlement_ref = (d.get_value('recipient_settlement_ref') || '').trim();
        const query = (d.get_value('warehouse_query') || '').trim(); if (!settlement_ref) return;
        const r = await frappe.call({ method:'ukrainian_integrations.shipment.nova_poshta.service.np_search_warehouses', args:{ settlement_ref, query, sender_profile: frm.doc.profile_name || frm.doc.name }});
        const items = (r.message && r.message.items) || []; if (!items.length) return;
        d.set_value('recipient_warehouse_ref', items[0].ref || '');
      });

      d.show();
    });
  }
});
