frappe.ui.form.on('RZ Delivery Sender Profile', {
  refresh(frm) {
    if (!frm.is_new()) {
      frm.add_custom_button('Перевірити токен', async () => {
        const response = await frappe.call({
          method: 'ukrainian_integrations.shipment.rozetka_delivery.service.verify_sender_profile',
          args: { sender_profile: frm.doc.name }
        });
        const result = (response && response.message) || {};
        frappe.msgprint({
          title: 'Rozetka Delivery',
          indicator: result.ok ? 'green' : 'red',
          message: result.ok
            ? `Токен дійсний. Статус партнера: ${frappe.utils.escape_html(result.status || '—')}`
            : 'Перевірка не пройдена'
        });
      });
    }

    frm.add_custom_button('Вибрати місто й відділення', () => {
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
          args: { query, carrier: frm.doc.carrier_id || '' }
        });
        cityItems = ((response.message && response.message.items) || []);
        setOptions('city_id', cityItems);
      };

      const searchDepartments = async () => {
        const cityId = dialog.get_value('city_id');
        if (!cityId) {
          frappe.msgprint('Спочатку виберіть місто.');
          return;
        }
        const response = await frappe.call({
          method: 'ukrainian_integrations.shipment.rozetka_delivery.service.rz_search_departments',
          args: {
            city_id: cityId,
            query: (dialog.get_value('department_query') || '').trim(),
            carrier: frm.doc.carrier_id || '',
            for_sender: 1
          }
        });
        departmentItems = ((response.message && response.message.items) || []);
        setOptions('department_id', departmentItems);
      };

      dialog = new frappe.ui.Dialog({
        title: 'Rozetka Delivery: адреса відправника',
        fields: [
          { fieldname: 'city_query', fieldtype: 'Data', label: 'Пошук міста', reqd: 1 },
          { fieldname: 'search_city', fieldtype: 'Button', label: 'Знайти міста', click: searchCities },
          { fieldname: 'city_id', fieldtype: 'Select', label: 'Місто', reqd: 1 },
          { fieldname: 'department_query', fieldtype: 'Data', label: 'Пошук відділення' },
          { fieldname: 'search_department', fieldtype: 'Button', label: 'Знайти відділення', click: searchDepartments },
          { fieldname: 'department_id', fieldtype: 'Select', label: 'Відділення', reqd: 1 }
        ],
        primary_action_label: 'Застосувати',
        primary_action(values) {
          const city = cityItems.find(item => item.id === values.city_id);
          const department = departmentItems.find(item => item.id === values.department_id);
          frm.set_value('sender_city_id', values.city_id);
          frm.set_value('sender_city_label', (city && city.label) || '');
          frm.set_value('sender_department_id', values.department_id);
          frm.set_value('sender_department_label', (department && department.label) || '');
          dialog.hide();
        }
      });
      dialog.show();
    });
  }
});
