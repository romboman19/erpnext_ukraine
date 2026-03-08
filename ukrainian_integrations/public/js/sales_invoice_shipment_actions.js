frappe.ui.form.on(Sales Invoice, {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button(NP: Створити ТТН, async () => {
      const d = new frappe.ui.Dialog({
        title: Нова Пошта: створити ТТН,
        fields: [
          { fieldname: recipient_city_ref, label: CityRecipient Ref, fieldtype: Data, reqd: 1 },
          { fieldname: recipient_warehouse_ref, label: RecipientAddress Ref, fieldtype: Data, reqd: 1 },
          { fieldname: weight, label: Вага, кг, fieldtype: Float, default: 1 },
          { fieldname: seats_amount, label: К-сть місць, fieldtype: Int, default: 1 },
          { fieldname: declared_cost, label: Оголошена вартість, fieldtype: Currency }
        ],
        primary_action_label: Створити,
        primary_action: async (values) => {
          try {
            const r = await frappe.call({
              method: ukrainian_integrations.shipment.nova_poshta.service.create_ttn_from_sales_invoice,
              args: {
                sales_invoice: frm.doc.name,
                recipient_city_ref: values.recipient_city_ref,
                recipient_warehouse_ref: values.recipient_warehouse_ref,
                weight: values.weight,
                seats_amount: values.seats_amount,
                declared_cost: values.declared_cost
              }
            });
            const m = (r && r.message) || {};
            frappe.msgprint(`ТТН створено: ${m.ttn_number || -}`);
            d.hide();
            frm.reload_doc();
          } catch (e) {
            frappe.msgprint({ title: Помилка, indicator: red, message: e.message || Не вдалося створити ТТН });
          }
        }
      });
      d.show();
    });

    frm.add_custom_button(UP: Створити відправлення, async () => {
      const d = new frappe.ui.Dialog({
        title: Укрпошта: створити відправлення,
        fields: [
          { fieldname: name, label: Отримувач, fieldtype: Data },
          { fieldname: phone, label: Телефон, fieldtype: Data },
          { fieldname: postcode, label: Індекс, fieldtype: Data },
          { fieldname: region, label: Область, fieldtype: Data },
          { fieldname: city, label: Місто, fieldtype: Data },
          { fieldname: street, label: Вулиця, fieldtype: Data },
          { fieldname: house, label: Будинок, fieldtype: Data },
          { fieldname: apartment, label: Квартира, fieldtype: Data },
          { fieldname: deliveryType, label: Тип доставки, fieldtype: Data, default: W2W },
          { fieldname: weight, label: Вага, кг, fieldtype: Float, default: 1 },
          { fieldname: declaredPrice, label: Оголошена вартість, fieldtype: Currency },
          { fieldname: description, label: Опис, fieldtype: Data }
        ],
        primary_action_label: Створити,
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
              method: ukrainian_integrations.shipment.ukr_poshta.service.create_shipment_from_sales_invoice,
              args: { sales_invoice: frm.doc.name, recipient, parcel }
            });
            const m = (r && r.message) || {};
            frappe.msgprint(`Відправлення створено: ${m.barcode || -}`);
            d.hide();
            frm.reload_doc();
          } catch (e) {
            frappe.msgprint({ title: Помилка, indicator: red, message: e.message || Не вдалося створити відправлення });
          }
        }
      });
      d.show();
    });
  }
});
