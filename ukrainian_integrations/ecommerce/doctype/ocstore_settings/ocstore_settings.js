frappe.ui.form.on("OcStore Settings", {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button(__("Test FTP Connections"), () => {
      frm.call("test_connections").then(() => frappe.show_alert({ message: __("Connection successful"), indicator: "green" }));
    });
    frm.add_custom_button(__("Export XML Now"), () => {
      frm.call("export_now", { force: 1 }).then((response) => {
        const result = response.message || {};
        frappe.msgprint(__("Exported {0} records", [result.records || 0]));
      });
    }, __("Actions"));
    frm.add_custom_button(__("Import Orders Now"), () => {
      frm.call("import_orders_now").then((response) => {
        const result = response.message || {};
        frappe.msgprint(__("Processed {0} order files", [result.files_processed || 0]));
      });
    }, __("Actions"));
  },
});
