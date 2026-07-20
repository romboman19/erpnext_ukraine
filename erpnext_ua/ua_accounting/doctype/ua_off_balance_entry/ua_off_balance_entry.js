frappe.ui.form.on("UA Off Balance Entry", {
	setup(frm) {
		frm.set_query("off_balance_account", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 1,
				ua_off_balance: 1,
			},
		}));
	},

	company(frm) {
		frm.set_value("off_balance_account", null);
	},
});
