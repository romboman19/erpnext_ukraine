frappe.listview_settings['UP Sender Profile'] = {
  onload(listview) {
    listview.page.add_menu_item('Створити відправлення (з профілю)', async () => {
      const r = await frappe.call({method:'ukrainian_integrations.shipment.ukr_poshta.service.up_sender_profiles_list'});
      const opts=((r.message&&r.message.items)||[]).map(x=>x.name);
      const d = new frappe.ui.Dialog({
        title:'УП відправлення з переліку профілів',
        fields:[
          {fieldname:'sender_profile',label:'Профіль',fieldtype:'Select',options:opts.join('\n'),reqd:1},
          {fieldname:'sales_invoice',label:'Sales Invoice',fieldtype:'Link',options:'Sales Invoice',reqd:1},
          {fieldname:'name',label:'Отримувач',fieldtype:'Data',reqd:1},
          {fieldname:'phone',label:'Телефон',fieldtype:'Data',reqd:1},
          {fieldname:'postcode',label:'Індекс',fieldtype:'Data',reqd:1},
          {fieldname:'region',label:'Область',fieldtype:'Data',reqd:1},
          {fieldname:'city',label:'Місто',fieldtype:'Data',reqd:1},
          {fieldname:'street',label:'Вулиця',fieldtype:'Data',reqd:1},
          {fieldname:'house',label:'Будинок',fieldtype:'Data',reqd:1},
        ],
        primary_action_label:'Створити',
        primary_action: async (v)=>{
          const recipient={name:v.name,phone:v.phone,postcode:v.postcode,region:v.region,city:v.city,street:v.street,house:v.house};
          const x=await frappe.call({method:'ukrainian_integrations.shipment.ukr_poshta.service.create_shipment_from_sales_invoice', args:{sender_profile:v.sender_profile, recipient, parcel:{}}});
          frappe.msgprint('Barcode: '+((x.message||{}).barcode||'-')); d.hide();
        }
      });
      d.show();
    });
  }
}
