frappe.ui.form.on('Sales Invoice', {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button('NP: Створити ТТН', async () => {
      let senderOptions = [];
      try {
        const p = await frappe.call({ method: 'ukrainian_integrations.shipment.nova_poshta.service.np_sender_profiles_list' });
        senderOptions = ((p.message && p.message.items) || []).map(x => x.name).filter(Boolean);
      } catch (_) {}

      const d = new frappe.ui.Dialog({
        title: 'Нова Пошта: створити ТТН',
        fields: [
          { fieldname: 'sender_profile', label: 'Профіль відправника', fieldtype: 'Select', options: senderOptions.join('\n') },
          { fieldname: 'recipient_name', label: 'Одержувач', fieldtype: 'Data', default: frm.doc.customer_name || frm.doc.customer, reqd: 1 },
          { fieldname: 'recipient_phone', label: 'Телефон', fieldtype: 'Data', default: frm.doc.contact_mobile || '', reqd: 1 },
          { fieldname: 'settlement_query', label: 'Пошук міста', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'recipient_settlement_ref', label: 'Settlement Ref', fieldtype: 'Data', read_only: 1 },
          { fieldname: 'recipient_city_ref', label: 'City Ref', fieldtype: 'Data', read_only: 1, reqd: 1 },
          { fieldname: 'warehouse_query', label: 'Пошук відділення/поштомата', fieldtype: 'Data' },
          { fieldname: 'recipient_warehouse_ref', label: 'Warehouse Ref', fieldtype: 'Data', reqd: 1 },
          { fieldname: 'weight', label: 'Вага, кг', fieldtype: 'Float', default: 1 },
          { fieldname: 'seats_amount', label: 'К-сть місць', fieldtype: 'Int', default: 1 },
          { fieldname: 'declared_cost', label: 'Оголошена вартість', fieldtype: 'Currency' }
        ],
        primary_action_label: 'Створити',
        primary_action: async (values) => {
          try {
            const r = await frappe.call({
              method: 'ukrainian_integrations.shipment.nova_poshta.service.create_ttn_from_sales_invoice',
              args: {
                sales_invoice: frm.doc.name,
                sender_profile: values.sender_profile,
                recipient_name: values.recipient_name,
                recipient_phone: values.recipient_phone,
                recipient_settlement_ref: values.recipient_settlement_ref,
                recipient_city_ref: values.recipient_city_ref,
                recipient_warehouse_ref: values.recipient_warehouse_ref,
                weight: values.weight,
                seats_amount: values.seats_amount,
                declared_cost: values.declared_cost
              }
            });
            const m = (r && r.message) || {};
            frappe.msgprint(`ТТН створено: ${m.ttn_number || '-'}`);
            d.hide();
            frm.reload_doc();
          } catch (e) {
            frappe.msgprint({ title: 'Помилка', indicator: 'red', message: (e && e.message) || 'Не вдалося створити ТТН' });
          }
        }
      });

      const sq = d.get_field('settlement_query');
      sq.$input.on('change', async () => {
        const q = (d.get_value('settlement_query') || '').trim();
        if (!q) return;
        try {
          const r = await frappe.call({
            method: 'ukrainian_integrations.shipment.nova_poshta.service.np_search_settlements',
            args: { query: q, sender_profile: d.get_value('sender_profile') }
          });
          const items = (r.message && r.message.items) || [];
          if (!items.length) return;
          const first = items[0];
          d.set_value('recipient_settlement_ref', first.settlement_ref || '');
          d.set_value('recipient_city_ref', first.city_ref || '');
        } catch (_) {}
      });

      const wq = d.get_field('warehouse_query');
      wq.$input.on('change', async () => {
        const settlement_ref = (d.get_value('recipient_settlement_ref') || '').trim();
        const query = (d.get_value('warehouse_query') || '').trim();
        if (!settlement_ref) return;
        try {
          const r = await frappe.call({
            method: 'ukrainian_integrations.shipment.nova_poshta.service.np_search_warehouses',
            args: { settlement_ref, query, sender_profile: d.get_value('sender_profile') }
          });
          const items = (r.message && r.message.items) || [];
          if (!items.length) return;
          const first = items[0];
          d.set_value('recipient_warehouse_ref', first.ref || '');
        } catch (_) {}
      });

      d.show();
    });

    frm.add_custom_button('UP: Створити відправлення', async () => {
      const d = new frappe.ui.Dialog({
        title: 'Укрпошта: створити відправлення',
        fields: [
          { fieldname: 'name', label: 'Отримувач', fieldtype: 'Data' },
          { fieldname: 'phone', label: 'Телефон', fieldtype: 'Data' },
          { fieldname: 'postcode', label: 'Індекс', fieldtype: 'Data' },
          { fieldname: 'region', label: 'Область', fieldtype: 'Data' },
          { fieldname: 'city', label: 'Місто', fieldtype: 'Data' },
          { fieldname: 'street', label: 'Вулиця', fieldtype: 'Data' },
          { fieldname: 'house', label: 'Будинок', fieldtype: 'Data' },
          { fieldname: 'apartment', label: 'Квартира', fieldtype: 'Data' },
          { fieldname: 'deliveryType', label: 'Тип доставки', fieldtype: 'Data', default: 'W2W' },
          { fieldname: 'weight', label: 'Вага, кг', fieldtype: 'Float', default: 1 },
          { fieldname: 'declaredPrice', label: 'Оголошена вартість', fieldtype: 'Currency' },
          { fieldname: 'description', label: 'Опис', fieldtype: 'Data' }
        ],
        primary_action_label: 'Створити',
        primary_action: async (values) => {
          try {
            const recipient = {
              name: values.name,
              phone: values.phone,
              postcode: values.postcode,
              region: values.region,
              city: values.city,
              street: values.street,
              house: values.house,
              apartment: values.apartment
            };
            const parcel = {
              deliveryType: values.deliveryType,
              weight: values.weight,
              declaredPrice: values.declaredPrice,
              description: values.description
            };
            const r = await frappe.call({
              method: 'ukrainian_integrations.shipment.ukr_poshta.service.create_shipment_from_sales_invoice',
              args: { sales_invoice: frm.doc.name, recipient, parcel }
            });
            const m = (r && r.message) || {};
            frappe.msgprint(`Відправлення створено: ${m.barcode || '-'}`);
            d.hide();
            frm.reload_doc();
          } catch (e) {
            frappe.msgprint({ title: 'Помилка', indicator: 'red', message: (e && e.message) || 'Не вдалося створити відправлення' });
          }
        }
      });
      d.show();
    });
  }
});
