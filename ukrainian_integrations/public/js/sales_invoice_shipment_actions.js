frappe.ui.form.on('Sales Invoice', {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button('NP: Створити ТТН', async () => {
      const idempotencyKey =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : frappe.utils.get_random(32);
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
                declared_cost: values.declared_cost,
                idempotency_key: idempotencyKey
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
      const idempotencyKey =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : frappe.utils.get_random(32);
      let upSenderOptions = [];
      try {
        const p = await frappe.call({ method: 'ukrainian_integrations.shipment.ukr_poshta.service.up_sender_profiles_list' });
        upSenderOptions = ((p.message && p.message.items) || []).map(x => x.name).filter(Boolean);
      } catch (_) {}
      const d = new frappe.ui.Dialog({
        title: 'Укрпошта: створити відправлення',
        fields: [
          { fieldname: 'up_sender_profile', label: 'Профіль відправника УП', fieldtype: 'Select', options: upSenderOptions.join('\n') },
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
              args: {
                sales_invoice: frm.doc.name,
                sender_profile: values.up_sender_profile,
                recipient,
                parcel,
                idempotency_key: idempotencyKey
              }
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

    if (frm.doc.docstatus === 1 && !frm.doc.rz_track_id) {
      frm.add_custom_button('Створити відправлення', async () => {
        const idempotencyKey =
          typeof crypto !== 'undefined' && crypto.randomUUID
            ? crypto.randomUUID()
            : frappe.utils.get_random(32);
        let senderOptions = [];
        try {
          const response = await frappe.call({
            method: 'ukrainian_integrations.shipment.rozetka_delivery.service.rz_sender_profiles_list',
            args: { company: frm.doc.company }
          });
          senderOptions = ((response.message && response.message.items) || [])
            .map(item => item.name)
            .filter(Boolean);
        } catch (_) {}
        if (!senderOptions.length) {
          frappe.msgprint('Спочатку створіть активний профіль RZ Delivery Sender Profile.');
          return;
        }

        const nameParts = (frm.doc.customer_name || frm.doc.customer || '').trim().split(/\s+/).filter(Boolean);
        let cityItems = [];
        let departmentItems = [];
        let dialog;
        const setOptions = (fieldname, items) => {
          dialog.set_df_property(
            fieldname,
            'options',
            [{ label: '', value: '' }].concat(items.map(item => ({ label: item.label, value: item.id })))
          );
          dialog.refresh_field(fieldname);
        };
        const searchCities = async () => {
          const query = (dialog.get_value('city_query') || '').trim();
          if (query.length < 2) {
            frappe.msgprint('Введіть щонайменше 2 символи назви міста.');
            return;
          }
          const response = await frappe.call({
            method: 'ukrainian_integrations.shipment.rozetka_delivery.service.rz_search_cities',
            args: {
              query,
              sender_profile: dialog.get_value('sender_profile')
            }
          });
          cityItems = ((response.message && response.message.items) || []);
          setOptions('recipient_city_id', cityItems);
        };
        const searchDepartments = async () => {
          const cityId = dialog.get_value('recipient_city_id');
          if (!cityId) {
            frappe.msgprint('Спочатку виберіть місто.');
            return;
          }
          const response = await frappe.call({
            method: 'ukrainian_integrations.shipment.rozetka_delivery.service.rz_search_departments',
            args: {
              city_id: cityId,
              query: (dialog.get_value('department_query') || '').trim(),
              sender_profile: dialog.get_value('sender_profile'),
              for_sender: 0
            }
          });
          departmentItems = ((response.message && response.message.items) || []);
          setOptions('recipient_department_id', departmentItems);
        };

        dialog = new frappe.ui.Dialog({
          title: 'Rozetka Delivery: створити відправлення',
          fields: [
            { fieldname: 'sender_profile', fieldtype: 'Select', label: 'Профіль відправника', options: senderOptions.join('\n'), default: senderOptions[0], reqd: 1 },
            { fieldname: 'recipient_first_name', fieldtype: 'Data', label: "Ім'я", default: nameParts[0] || '', reqd: 1 },
            { fieldname: 'recipient_middle_name', fieldtype: 'Data', label: 'По батькові', default: nameParts.length > 2 ? nameParts.slice(1, -1).join(' ') : '' },
            { fieldname: 'recipient_last_name', fieldtype: 'Data', label: 'Прізвище', default: nameParts.length > 1 ? nameParts[nameParts.length - 1] : '', reqd: 1 },
            { fieldname: 'recipient_phone', fieldtype: 'Data', label: 'Телефон', default: frm.doc.contact_mobile || frm.doc.contact_phone || '', reqd: 1 },
            { fieldname: 'city_query', fieldtype: 'Data', label: 'Пошук міста', reqd: 1 },
            { fieldname: 'search_city', fieldtype: 'Button', label: 'Знайти міста', click: searchCities },
            { fieldname: 'recipient_city_id', fieldtype: 'Select', label: 'Місто', reqd: 1 },
            { fieldname: 'department_query', fieldtype: 'Data', label: 'Пошук відділення' },
            { fieldname: 'search_department', fieldtype: 'Button', label: 'Знайти відділення', click: searchDepartments },
            { fieldname: 'recipient_department_id', fieldtype: 'Select', label: 'Відділення', reqd: 1 },
            { fieldname: 'parcel_section', fieldtype: 'Section Break', label: 'Посилка' },
            { fieldname: 'weight', fieldtype: 'Float', label: 'Вага, кг', default: 1, reqd: 1 },
            { fieldname: 'length', fieldtype: 'Float', label: 'Довжина, см', default: 10, reqd: 1 },
            { fieldname: 'width', fieldtype: 'Float', label: 'Ширина, см', default: 10, reqd: 1 },
            { fieldname: 'height', fieldtype: 'Float', label: 'Висота, см', default: 5, reqd: 1 },
            { fieldname: 'places', fieldtype: 'Int', label: 'Кількість місць', default: 1, reqd: 1 },
            { fieldname: 'insurance_cost', fieldtype: 'Currency', label: 'Оголошена вартість', default: frm.doc.grand_total || 1, reqd: 1 },
            { fieldname: 'cost', fieldtype: 'Currency', label: 'Післяплата', default: 0 },
            { fieldname: 'delivery_payer', fieldtype: 'Select', label: 'Платник доставки', options: 'sender\nreceiver', default: 'sender', reqd: 1 },
            { fieldname: 'description', fieldtype: 'Small Text', label: 'Опис', default: `Замовлення ${frm.doc.name}` }
          ],
          primary_action_label: 'Створити',
          primary_action: async values => {
            const response = await frappe.call({
              method: 'ukrainian_integrations.shipment.rozetka_delivery.service.create_track_from_sales_invoice',
              args: {
                sales_invoice: frm.doc.name,
                recipient_city_id: values.recipient_city_id,
                recipient_department_id: values.recipient_department_id,
                recipient_first_name: values.recipient_first_name,
                recipient_middle_name: values.recipient_middle_name,
                recipient_last_name: values.recipient_last_name,
                recipient_phone: values.recipient_phone,
                sender_profile: values.sender_profile,
                description: values.description,
                parcel: {
                  weight: values.weight,
                  length: values.length,
                  width: values.width,
                  height: values.height,
                  places: values.places,
                  insurance_cost: values.insurance_cost,
                  cost: values.cost,
                  delivery_payer: values.delivery_payer
                },
                idempotency_key: idempotencyKey
              }
            });
            const result = (response && response.message) || {};
            frappe.msgprint(`Відправлення створено: ${frappe.utils.escape_html(result.track_id || '—')}`);
            dialog.hide();
            frm.reload_doc();
          }
        });
        dialog.show();
      }, 'Rozetka Delivery');
    }

    if (frm.doc.rz_track_id) {
      frm.add_custom_button('Оновити статус', async () => {
        const response = await frappe.call({
          method: 'ukrainian_integrations.shipment.rozetka_delivery.service.sync_one_invoice_status',
          args: { sales_invoice: frm.doc.name }
        });
        const result = (response && response.message) || {};
        frappe.show_alert({ message: `Статус: ${result.status || result.status_code || '—'}`, indicator: 'green' });
        frm.reload_doc();
      }, 'Rozetka Delivery');
      frm.add_custom_button('Завантажити етикетку', () => {
        const url =
          '/api/method/ukrainian_integrations.shipment.rozetka_delivery.service.download_track_label' +
          `?track_id=${encodeURIComponent(frm.doc.rz_track_id)}` +
          `&sender_profile=${encodeURIComponent(frm.doc.rz_sender_profile || '')}`;
        window.open(url, '_blank', 'noopener');
      }, 'Rozetka Delivery');
    }
  }
});
