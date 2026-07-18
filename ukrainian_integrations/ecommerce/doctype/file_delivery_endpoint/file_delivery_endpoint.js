frappe.ui.form.on("File Delivery Endpoint", {
  refresh(frm) {
    if (frm.is_new()) {
      return;
    }
    frm.add_custom_button(__("Test Connection"), async () => {
      const response = await frm.call("test_connection");
      if (response.message?.ok) {
        frappe.show_alert({ message: __("Connection successful"), indicator: "green" });
      }
    });
  },
});
