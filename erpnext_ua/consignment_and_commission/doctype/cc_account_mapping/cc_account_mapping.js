frappe.ui.form.on("CC Account Mapping", {
	refresh(frm) {
		if (!frm.doc.company) {
			return;
		}
		frm.add_custom_button(__("Apply Ukrainian PSBO Mapping"), async () => {
			const result = await frappe.call({
				method:
					"erpnext_ua.consignment_and_commission.setup." +
					"psbo_accounting.setup_psbo_account_mapping",
				args: { company: frm.doc.company, overwrite: 1 },
				freeze: true,
				freeze_message: __("Applying accounts 024, 702, 703, 704 and 685..."),
			});
			frappe.set_route("Form", "CC Account Mapping", result.message.name);
		}, __("Actions"));
	},
});
