frappe.ui.form.on("Customer Identification Settings", {
	refresh(frm) {
		load_turbosms_senders(frm);
	},

	sms_enabled(frm) {
		if (frm.doc.sms_enabled) load_turbosms_senders(frm);
	},
});

async function load_turbosms_senders(frm) {
	if (!frm.doc.sms_enabled) return;
	try {
		const response = await frappe.call({
			method: "ukrainian_integrations.pbx_sms.sms.turbosms.get_sender_options",
		});
		const config = response.message || {};
		const senders = config.senders || [];
		frm.set_df_property("sms_sender", "options", ["", ...senders].join("\n"));
		frm.set_df_property(
			"sms_sender",
			"description",
			config.enabled
				? __("Активні відправники з TurboSMS Settings")
				: __("Спочатку увімкніть TurboSMS Settings")
		);
		if (!frm.doc.sms_sender && config.default_sender) {
			await frm.set_value("sms_sender", config.default_sender);
		}
	} catch (error) {
		frm.set_df_property(
			"sms_sender",
			"description",
			__("Не вдалося завантажити відправників із TurboSMS Settings")
		);
	}
}
