frappe.ui.form.on("Ecommerce File Exchange", {
  refresh(frm) {
    if (frm.is_new() || frm.doc.direction !== "Import" || !frm.doc.exchange_file) return;
    if (["Processed", "Processing"].includes(frm.doc.status)) return;

    frm.add_custom_button(__("Process Import"), () => {
      frappe.call({
        method: "ukrainian_integrations.ecommerce.api.process_exchange_file",
        args: { exchange: frm.doc.name },
        freeze: true,
        freeze_message: __("Processing ecommerce file"),
      }).then((r) => {
        frm.reload_doc();
        frappe.msgprint(JSON.stringify(r.message || {}, null, 2));
      });
    });
  },
});
