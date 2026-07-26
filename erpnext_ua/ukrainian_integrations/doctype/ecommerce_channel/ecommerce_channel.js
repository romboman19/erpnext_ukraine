frappe.ui.form.on("Ecommerce Channel", {
  provider(frm) {
    if (frm.doc.provider === "ocStore") {
      frm.set_value({
        catalog_transport: "XML",
        stock_transport: "XML",
        orders_transport: "XML",
        customers_transport: "Disabled",
        order_status_transport: "Disabled",
        catalog_xml_profile: "ERPNext Exchange XML v1",
      });
    } else if (frm.doc.provider === "Shop-Express") {
      frm.set_value({
        catalog_transport: "XML",
        stock_transport: "API",
        orders_transport: "API",
        customers_transport: "API",
        order_status_transport: "Disabled",
        catalog_xml_profile: "Shop-Express YML",
      });
    }
  },
  refresh(frm) {
    if (frm.is_new()) return;

    if (frm.doc.provider === "Shop-Express") {
      frm.add_custom_button(__("Test API"), () => {
        call_ecommerce("erpnext_ua.ecommerce.api.test_channel", frm)
          .then((r) => frappe.msgprint(JSON.stringify(r.message || {}, null, 2)));
      }, __("E-commerce"));
    }

    frm.add_custom_button(__("Generate Catalog File"), () => {
      call_ecommerce("erpnext_ua.ecommerce.api.generate_catalog_file", frm)
        .then((r) => {
          if (r.message && r.message.exchange) {
            frappe.set_route("Form", "Ecommerce File Exchange", r.message.exchange);
          }
        });
    }, __("E-commerce"));

    if (frm.doc.catalog_transport === "API") {
      frm.add_custom_button(__("Sync Catalog Now"), () => {
        call_ecommerce("erpnext_ua.ecommerce.api.sync_channel_catalog", frm)
          .then((r) => frappe.msgprint(JSON.stringify(r.message || {}, null, 2)));
      }, __("E-commerce"));
    }

    if (frm.doc.orders_transport === "API") {
      frm.add_custom_button(__("Sync Orders Now"), () => {
        call_ecommerce("erpnext_ua.ecommerce.api.sync_channel_orders", frm)
          .then((r) => frappe.msgprint(JSON.stringify(r.message || {}, null, 2)));
      }, __("E-commerce"));
    }

    if (frm.doc.customers_transport === "API") {
      frm.add_custom_button(__("Sync Customers Now"), () => {
        call_ecommerce("erpnext_ua.ecommerce.api.sync_channel_customers", frm)
          .then((r) => frappe.msgprint(JSON.stringify(r.message || {}, null, 2)));
      }, __("E-commerce"));
    }

    if (frm.doc.stock_transport === "API") {
      frm.add_custom_button(__("Sync Prices and Stock Now"), () => {
        call_ecommerce("erpnext_ua.ecommerce.api.sync_channel_stock", frm)
          .then((r) => frappe.msgprint(JSON.stringify(r.message || {}, null, 2)));
      }, __("E-commerce"));
    }


    if (frm.doc.stock_transport === "XML") {
      frm.add_custom_button(__("Generate Prices and Stock File"), () => {
        call_ecommerce("erpnext_ua.ecommerce.api.generate_stock_file", frm)
          .then((r) => {
            if (r.message && r.message.exchange) {
              frappe.set_route("Form", "Ecommerce File Exchange", r.message.exchange);
            }
          });
      }, __("E-commerce"));
    }

    if (frm.doc.order_status_transport === "API") {
      frm.add_custom_button(__("Sync Order Statuses Now"), () => {
        call_ecommerce("erpnext_ua.ecommerce.api.sync_channel_order_statuses", frm)
          .then((r) => frappe.msgprint(JSON.stringify(r.message || {}, null, 2)));
      }, __("E-commerce"));
    }
  },
});

function call_ecommerce(method, frm) {
  return frappe.call({
    method,
    args: { channel: frm.doc.name },
    freeze: true,
    freeze_message: __("Synchronizing ecommerce channel"),
  });
}
