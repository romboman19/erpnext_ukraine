(() => {
	const SOURCE_DOCTYPES = ["Purchase Receipt", "Stock Entry", "Delivery Note", "Item"];

	function preview_args(frm, dialog) {
		return {
			source_doctype: frm.doctype,
			source_name: frm.doc.name,
			warehouse: dialog.get_value("warehouse"),
			price_list: dialog.get_value("price_list"),
			promotional_price_list: dialog.get_value("promotional_price_list") || "",
			label_size: dialog.get_value("label_size"),
			template_mode: dialog.get_value("template_mode"),
		};
	}

	function load_preview(frm, dialog) {
		frappe.call({
			method: "erpnext_ua.ua_price_tags.service.preview_source",
			args: preview_args(frm, dialog),
			freeze: true,
			freeze_message: __("Визначаємо чинні ціни…"),
			callback(response) {
				const rows = (response.message && response.message.rows) || [];
				dialog.fields_dict.items.df.data = rows.map((row) => ({
					selected: row.price_missing ? 0 : 1,
					source_row: row.source_row,
					item_code: row.item_code,
					item_name: row.item_name,
					warehouse: row.warehouse,
					source_qty: row.source_qty,
					regular_price: row.regular_price,
					selling_price: row.selling_price,
					is_promotional: row.is_promotional,
					copies: row.copies || 1,
					price_status: row.price_missing ? __("Немає ціни") : "",
				}));
				dialog.fields_dict.items.grid.refresh();
			},
		});
	}

	function build_dialog(frm, config) {
		let dialog;
		const reload = () => dialog && dialog.get_value("price_list") && load_preview(frm, dialog);
		dialog = new frappe.ui.Dialog({
			title: __("Створити пакет цінників"),
			size: "extra-large",
			fields: [
				{fieldname: "price_list", fieldtype: "Link", options: "Price List", label: __("Роздрібний прайс-лист"), reqd: 1, default: config.price_list, onchange: reload},
				{fieldname: "promotional_price_list", fieldtype: "Link", options: "Price List", label: __("Акційний прайс-лист"), default: config.promotional_price_list, onchange: reload},
				{fieldname: "template_mode", fieldtype: "Select", options: "Auto Price Tag\nPackaging Label", label: __("Шаблон"), reqd: 1, default: "Auto Price Tag", onchange: reload},
				{fieldname: "label_size", fieldtype: "Select", options: "40×25 mm", label: __("Розмір"), reqd: 1, default: config.label_size},
				{fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: __("Склад (якщо не заданий у документі)"), onchange: reload},
				{fieldname: "copies_mode", fieldtype: "Select", label: __("Кількість копій"), options: "One\nSource Quantity\nManual Copies", default: "One", reqd: 1},
				{fieldname: "items", fieldtype: "Table", label: __("Товари"), cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true, fields: [
					{fieldname: "selected", fieldtype: "Check", label: __("Друк"), in_list_view: 1},
					{fieldname: "source_row", fieldtype: "Data", hidden: 1},
					{fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Товар"), read_only: 1, in_list_view: 1, columns: 2},
					{fieldname: "item_name", fieldtype: "Data", label: __("Назва"), read_only: 1, in_list_view: 1, columns: 2},
					{fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: __("Склад"), read_only: 1},
					{fieldname: "source_qty", fieldtype: "Float", label: __("К-сть"), read_only: 1, in_list_view: 1},
					{fieldname: "regular_price", fieldtype: "Currency", label: __("Звичайна"), read_only: 1},
					{fieldname: "selling_price", fieldtype: "Currency", label: __("Ціна"), read_only: 1, in_list_view: 1},
					{fieldname: "is_promotional", fieldtype: "Check", label: __("Акція"), read_only: 1, in_list_view: 1},
					{fieldname: "copies", fieldtype: "Int", label: __("Копій"), default: 1, in_list_view: 1},
					{fieldname: "price_status", fieldtype: "Data", label: __("Стан"), read_only: 1, in_list_view: 1},
				]},
			],
			primary_action_label: __("Створити пакет"),
			primary_action(values) {
				const selected = (values.items || []).filter((row) => row.selected).map((row) => ({
					source_row: row.source_row,
					copies: row.copies,
				}));
				if (!selected.length) {
					frappe.msgprint(__("Оберіть хоча б один товар."));
					return;
				}
				frappe.call({
					method: "erpnext_ua.ua_price_tags.service.create_source_jobs",
					args: {...preview_args(frm, dialog), selected, copies_mode: values.copies_mode},
					freeze: true,
					freeze_message: __("Фіксуємо ціни та створюємо пакети…"),
					callback(response) {
						const jobs = response.message || [];
						dialog.hide();
						if (jobs.length > 1) frappe.show_alert(__("Створено пакетів: {0}", [jobs.length]));
						if (jobs.length) frappe.set_route("Form", "Price Tag Print Job", jobs[0]);
					},
				});
			},
		});
		dialog.show();
		load_preview(frm, dialog);
	}

	function open_dialog(frm) {
		frappe.call({
			method: "erpnext_ua.ua_price_tags.service.get_configuration",
			callback: (response) => build_dialog(frm, response.message || {}),
		});
	}

	SOURCE_DOCTYPES.forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				if (frm.is_new()) return;
				const stockPurpose = frm.doc.purpose || frm.doc.stock_entry_type;
				if (doctype === "Stock Entry" && stockPurpose && stockPurpose !== "Material Transfer") return;
				frm.add_custom_button(__("Цінники"), () => open_dialog(frm), __("Друк"));
			},
		});
	});
})();
