const OCSTORE_EXPORT_ENTITIES = new Set(["Products", "Prices", "Stock", "Photos"]);

function ocstore_entity_is_enabled(frm, entities) {
  return (frm.doc.sync_entities || []).some(
    (row) => entities.has(row.entity) && Number(row.enabled || 0) === 1 && row.method === "File",
  );
}

function with_saved_ocstore_settings(frm, action) {
  if (frm.is_dirty()) {
    return frm.save().then(action);
  }
  return action();
}

frappe.ui.form.on("OcStore Settings", {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button(__("Test FTP Connections"), () => {
      with_saved_ocstore_settings(frm, () =>
        frm.call("test_connections").then(() =>
          frappe.show_alert({ message: __("Connection successful"), indicator: "green" }),
        ),
      );
    });
    frm.add_custom_button(__("Export XML Now"), () => {
      if (!ocstore_entity_is_enabled(frm, OCSTORE_EXPORT_ENTITIES)) {
        frappe.msgprint({
          title: __("ocStore export is not configured"),
          message: __(
            "Enable at least one ocStore export entity (Products, Prices, Stock or Photos), save the settings and try again.",
          ),
          indicator: "orange",
        });
        return;
      }
      with_saved_ocstore_settings(frm, () =>
        frm.call("export_now", { force: 1 }).then((response) => {
          const result = response.message || {};
          frappe.msgprint(__("Exported {0} records", [result.records || 0]));
        }),
      );
    }, __("Actions"));
    frm.add_custom_button(__("Import Orders Now"), () => {
      if (!ocstore_entity_is_enabled(frm, new Set(["Orders"]))) {
        frappe.msgprint({
          title: __("ocStore order import is not configured"),
          message: __(
            "Enable the ocStore Orders import entity with the File method, save the settings and try again.",
          ),
          indicator: "orange",
        });
        return;
      }
      with_saved_ocstore_settings(frm, () =>
        frm.call("import_orders_now").then((response) => {
          const result = response.message || {};
          frappe.msgprint(__("Processed {0} order files", [result.files_processed || 0]));
        }),
      );
    }, __("Actions"));
  },
});
