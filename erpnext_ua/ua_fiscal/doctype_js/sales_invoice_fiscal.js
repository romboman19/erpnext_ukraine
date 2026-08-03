frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || (!frm.doc.is_pos && !frm.doc.ua_ecommerce_channel)) return;

		frappe.db.get_value(
			"PRRO Receipt",
			{ sales_invoice: frm.doc.name },
			["name", "status", "fiscal_number", "qr_data"]
		).then((r) => {
			const rec = r.message;
			if (rec && rec.status === "Fiscalized") {
				frm.dashboard.set_headline(
					__("Фіскальний чек: <b>{0}</b> (№ {1})", [rec.name, rec.fiscal_number])
				);
				frm.add_custom_button(__("Відкрити чек ПРРО"), () => {
					frappe.set_route("Form", "PRRO Receipt", rec.name);
				}, __("ПРРО"));
			} else {
				frm.add_custom_button(__("Фіскалізувати чек"), () => {
					frappe.call({
						method: "erpnext_ua.ua_fiscal.api.fiscalize_sales_invoice",
						args: { sales_invoice: frm.doc.name },
						freeze: true,
						freeze_message: __("Фіскалізація…"),
					}).then((res) => {
						if (res.message) {
							frappe.show_alert({ message: __("Чек фіскалізовано"), indicator: "green" });
							frm.reload_doc();
						}
					});
				}, __("ПРРО"));
			}
		});
	},
});
