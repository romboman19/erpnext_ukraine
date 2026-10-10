frappe.listview_settings["UA Tax Deadline"] = {
	get_indicator(doc) {
		const colors = {
			"Заплановано": "blue",
			"Скоро термін": "orange",
			"Прострочено": "red",
			"Виконано": "green",
			"Скасовано": "gray",
		};
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
