frappe.ui.form.on("PRRO Fiscalization Job", {
	refresh(frm) {
		if (frm.is_new() || ["Completed", "Cancelled"].includes(frm.doc.status)) return;
		if (!frappe.user_roles.some((role) => ["System Manager", "Accounts Manager"].includes(role))) return;

		frm.add_custom_button(__("Повторити безпечно"), async () => {
			await frappe.call({
				method: "erpnext_ua.ua_fiscal.api.retry_fiscalization_job",
				args: { job_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Звірка з ДПС і повтор фіскалізації…"),
			});
			await frm.reload_doc();
		}, __("ПРРО"));
	},
});
