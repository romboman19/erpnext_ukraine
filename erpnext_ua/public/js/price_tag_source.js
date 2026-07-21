(() => {
	const SOURCE_DOCTYPES = ["Purchase Receipt", "Stock Entry", "Delivery Note", "Item"];
	const RECEIPT_PRINT_FORMAT = "Прибуткова накладна (UA)";

	function print_view_url(doctype, name, format, noLetterhead = 1) {
		const query = new URLSearchParams({
			doctype,
			name,
			format,
			no_letterhead: String(noLetterhead),
		});
		return `/printview?${query.toString()}`;
	}

	function open_job_print(jobName) {
		frappe.db.get_value("Price Tag Print Job", jobName, "print_format").then((response) => {
			const format = response.message && response.message.print_format;
			if (!format) {
				frappe.msgprint(__("Для пакета {0} не задано формат друку.", [jobName]));
				return;
			}
			window.location.assign(print_view_url("Price Tag Print Job", jobName, format));
		});
	}

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
						if (jobs.length) open_job_print(jobs[0]);
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

	function round_up_price(cost, markup, step) {
		const raw = flt(cost) * (1 + flt(markup) / 100);
		const unit = flt(step);
		return unit > 0 ? flt(Math.ceil((raw - 1e-9) / unit) * unit, 2) : flt(raw, 2);
	}

	function recalculate_receipt_prices(dialog) {
		const markup = dialog.get_value("markup_percent");
		const step = dialog.get_value("rounding_step");
		(dialog.fields_dict.items.df.data || []).forEach((row) => {
			row.new_price = round_up_price(row.unit_cost, markup, step);
		});
		dialog.fields_dict.items.grid.refresh();
	}

	function completion_links(result) {
		const links = [];
		if (result.purchase_invoice) {
			links.push(`<a href="/app/purchase-invoice/${encodeURIComponent(result.purchase_invoice)}">${__("Рахунок закупівлі")} ${frappe.utils.escape_html(result.purchase_invoice)}</a>`);
		}
		(result.price_tag_prints || []).forEach((job) => {
			const url = print_view_url("Price Tag Print Job", job.name, job.print_format);
			links.push(`<a href="${url}">${__("Друкувати цінники")} ${frappe.utils.escape_html(job.name)}</a>`);
		});
		return links.join("<br>");
	}

	function open_receipt_completion(frm) {
		frappe.call({
			method: "erpnext_ua.ua_receiving.service.preview_receipt_completion",
			args: {receipt_name: frm.doc.name},
			freeze: true,
			freeze_message: __("Розраховуємо роздрібні ціни…"),
			callback(response) {
				const preview = response.message || {};
				let dialog;
				dialog = new frappe.ui.Dialog({
					title: __("Завершити приймання товару"),
					size: "extra-large",
					fields: [
						{fieldname: "price_list", fieldtype: "Link", options: "Price List", label: __("Роздрібний прайс-лист"), reqd: 1, default: preview.price_list, read_only: 1},
						{fieldname: "markup_percent", fieldtype: "Percent", label: __("Націнка, %"), default: preview.markup_percent, onchange: () => recalculate_receipt_prices(dialog)},
						{fieldname: "rounding_step", fieldtype: "Currency", label: __("Крок округлення"), default: preview.rounding_step, onchange: () => recalculate_receipt_prices(dialog)},
						{fieldname: "create_purchase_invoice", fieldtype: "Check", label: __("Створити чернетку рахунку закупівлі"), default: preview.create_purchase_invoice},
						{fieldname: "items", fieldtype: "Table", label: __("Ціни й цінники"), reqd: 1, cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true, fields: [
							{fieldname: "selected", fieldtype: "Check", label: __("Підтвердити"), default: 1, in_list_view: 1},
							{fieldname: "source_row", fieldtype: "Data", hidden: 1},
							{fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Товар"), read_only: 1, in_list_view: 1, columns: 2},
							{fieldname: "item_name", fieldtype: "Data", label: __("Назва"), read_only: 1, in_list_view: 1, columns: 2},
							{fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: __("Склад"), read_only: 1},
							{fieldname: "received_qty", fieldtype: "Float", label: __("Прийнято"), read_only: 1, in_list_view: 1},
							{fieldname: "unit_cost", fieldtype: "Currency", label: __("Собівартість"), read_only: 1, in_list_view: 1},
							{fieldname: "current_price", fieldtype: "Currency", label: __("Чинна ціна"), read_only: 1},
							{fieldname: "new_price", fieldtype: "Currency", label: __("Нова ціна"), reqd: 1, in_list_view: 1},
							{fieldname: "copies", fieldtype: "Int", label: __("Цінників"), reqd: 1, in_list_view: 1},
						]},
					],
					primary_action_label: __("Підтвердити ціни та створити документи"),
					primary_action(values) {
						const selected = (values.items || []).filter((row) => row.selected);
						if (!selected.length || selected.some((row) => flt(row.new_price) <= 0)) {
							frappe.msgprint(__("Оберіть товари та вкажіть додатні роздрібні ціни."));
							return;
						}
						frappe.call({
							method: "erpnext_ua.ua_receiving.service.complete_receipt",
							args: {
								receipt_name: frm.doc.name,
								price_list: values.price_list,
								create_purchase_invoice: values.create_purchase_invoice ? 1 : 0,
								prices: values.items,
							},
							freeze: true,
							freeze_message: __("Фіксуємо ціни, цінники та рахунок…"),
							callback(result_response) {
								const result = result_response.message || {};
								dialog.hide();
								frm.reload_doc();
								frappe.msgprint({
									title: __("Приймання завершено"),
									indicator: "green",
									message: completion_links(result) || __("Ціни підтверджено."),
								});
							},
						});
					},
				});
				dialog.fields_dict.items.df.data = (preview.rows || []).map((row) => ({
					...row,
					selected: 1,
					new_price: row.suggested_price,
				}));
				dialog.show();
				dialog.fields_dict.items.grid.refresh();
			},
		});
	}

	SOURCE_DOCTYPES.forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				if (frm.is_new()) return;
				if (doctype === "Purchase Receipt") {
					frm.add_custom_button(__("Контрольний лист A4"), () => {
						window.open(
							print_view_url("Purchase Receipt", frm.doc.name, RECEIPT_PRINT_FORMAT, 0),
							"_blank",
							"noopener",
						);
					}, __("Друк"));
				}
				if (doctype === "Purchase Receipt" && frm.doc.docstatus === 1 && !frm.doc.ua_receiving_completed) {
					frm.add_custom_button(__("Завершити приймання"), () => open_receipt_completion(frm), __("Приймання"));
				}
				if (doctype === "Purchase Receipt" && frm.doc.ua_purchase_invoice) {
					frm.add_custom_button(__("Рахунок закупівлі"), () => frappe.set_route("Form", "Purchase Invoice", frm.doc.ua_purchase_invoice), __("Приймання"));
				}
				if (doctype === "Purchase Receipt" && frm.doc.ua_price_tag_jobs) {
					const firstJob = String(frm.doc.ua_price_tag_jobs).split("\n").filter(Boolean)[0];
					if (firstJob) {
						frm.add_custom_button(__("Друкувати цінники"), () => open_job_print(firstJob), __("Друк"));
					}
				}
				if (doctype === "Purchase Receipt" && frm.doc.docstatus !== 1) return;
				const stockPurpose = frm.doc.purpose || frm.doc.stock_entry_type;
				if (doctype === "Stock Entry" && stockPurpose && stockPurpose !== "Material Transfer") return;
				frm.add_custom_button(__("Цінники"), () => open_dialog(frm), __("Друк"));
			},
		});
	});
})();
