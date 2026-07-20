frappe.listview_settings["Price Tag Print Job"] = {
	add_fields: ["status", "template_type", "total_labels"],
	get_indicator(doc) {
		const colors = {Draft: "gray", Ready: "orange", Printed: "green", Error: "red"};
		return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
