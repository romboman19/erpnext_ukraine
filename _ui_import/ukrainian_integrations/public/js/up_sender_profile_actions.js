frappe.ui.form.on('UP Sender Profile', {
  refresh(frm) {
    frm.add_custom_button('Створити відправлення (довільно)', async () => {
      const idempotencyKey = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : frappe.utils.get_random(32);
      const d = new frappe.ui.Dialog({
        title: 'УП відправлення із профілю',
        fields: [
                    {fieldname:'name', label:'Отримувач', fieldtype:'Data', reqd:1},
          {fieldname:'phone', label:'Телефон', fieldtype:'Data', reqd:1},
          {fieldname:'postcode', label:'Індекс', fieldtype:'Data', reqd:1},
          {fieldname:'region', label:'Область', fieldtype:'Data', reqd:1},
          {fieldname:'city', label:'Місто', fieldtype:'Data', reqd:1},
          {fieldname:'street', label:'Вулиця', fieldtype:'Data', reqd:1},
          {fieldname:'house', label:'Будинок', fieldtype:'Data', reqd:1},
        ],
        primary_action_label: 'Створити',
        primary_action: async (v) => {
          const recipient = {name:v.name, phone:v.phone, postcode:v.postcode, region:v.region, city:v.city, street:v.street, house:v.house};
          const r = await frappe.call({
            method: 'ukrainian_integrations.shipment.ukr_poshta.service.create_shipment_standalone',
            args: {sender_profile: frm.doc.profile_name || frm.doc.name, recipient, parcel:{}, idempotency_key: idempotencyKey}
          });
          frappe.msgprint('Barcode: ' + ((r.message||{}).barcode || '-'));
          d.hide();
        }
      });
      d.show();
    });
  }
});
