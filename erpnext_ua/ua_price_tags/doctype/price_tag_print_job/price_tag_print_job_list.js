frappe.listview_settings["Price Tag Print Job"] = {
	add_fields: ["status", "template_type", "total_labels", "print_format"],
	get_indicator(doc) {
		const colors = {Draft: "gray", Ready: "orange", Printed: "green", Error: "red"};
		return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
	},
	button: {
		show(doc) {
			return ["Ready", "Printed"].includes(doc.status) && doc.print_format;
		},
		get_label() {
			return __("Друк");
		},
		get_description(doc) {
			return __("Відкрити друк пакета {0}", [doc.name]);
		},
		action(doc) {
			const query = new URLSearchParams({
				doctype: "Price Tag Print Job",
				name: doc.name,
				format: doc.print_format,
				no_letterhead: "1",
			});
			window.location.assign(`/printview?${query.toString()}`);
		},
	},
};
