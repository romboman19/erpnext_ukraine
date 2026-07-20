frappe.pages["price-tag-stock"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Залишки та друк цінників"),
		single_column: true,
	});
	const state = {rows: []};
	const escape = (value) => frappe.utils.escape_html(String(value == null ? "" : value));
	const money = (value, currency) => value == null ? "—" : format_currency(value, currency || "UAH");

	const warehouse = page.add_field({fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: __("Склад"), reqd: 1, change: refresh});
	const price_list = page.add_field({fieldname: "price_list", fieldtype: "Link", options: "Price List", label: __("Прайс-лист"), reqd: 1, change: refresh});
	const promotional_price_list = page.add_field({fieldname: "promotional_price_list", fieldtype: "Link", options: "Price List", label: __("Акційний прайс-лист"), change: refresh});
	const template_mode = page.add_field({
		fieldname: "template_mode",
		fieldtype: "Select",
		options: "Auto Price Tag\nPackaging Label",
		label: __("Шаблон"),
		default: "Auto Price Tag",
		change: () => {if (state.rows.length) render(state.rows);},
	});
	const brand = page.add_field({fieldname: "brand", fieldtype: "Link", options: "Brand", label: __("Бренд"), change: refresh});
	const item_group = page.add_field({fieldname: "item_group", fieldtype: "Link", options: "Item Group", label: __("Група товарів"), change: refresh});
	const search_text = page.add_field({fieldname: "search_text", fieldtype: "Data", label: __("Код, назва або штрихкод")});

	$(page.body).html(`
		<style>
			.price-tag-stock-wrap{padding:16px 0}.price-tag-stock-table{width:100%;border-collapse:collapse;background:var(--card-bg)}
			.price-tag-stock-table th,.price-tag-stock-table td{padding:9px;border-bottom:1px solid var(--border-color);vertical-align:middle}
			.price-tag-stock-table th{position:sticky;top:0;background:var(--subtle-fg);z-index:1;text-align:left}
			.price-tag-stock-table .num{text-align:right}.price-tag-stock-table input[type=number]{width:72px}
			.price-tag-empty{padding:48px;text-align:center;color:var(--text-muted)}
		</style>
		<div class="price-tag-stock-wrap"><div class="price-tag-stock-result"></div></div>
	`);
	const result = $(page.body).find(".price-tag-stock-result");

	function render(rows) {
		state.rows = rows || [];
		if (!state.rows.length) {
			result.html(`<div class="price-tag-empty">${__("Товарів із позитивним залишком не знайдено.")}</div>`);
			return;
		}
		const packaging = template_mode.get_value() === "Packaging Label";
		const body = state.rows.map((row, index) => {
			const disabled = !packaging && row.selling_price == null;
			return `
				<tr data-index="${index}">
					<td><input class="price-tag-select" type="checkbox" ${disabled ? "disabled" : "checked"}></td>
					<td><a href="/app/item/${encodeURIComponent(row.item_code)}">${escape(row.item_code)}</a><br><small>${escape(row.barcode)}</small></td>
					<td>${escape(row.item_name)}<br><small>${escape(row.brand || row.item_group)}</small></td>
					<td class="num">${escape(row.stock_qty)} ${escape(row.uom)}</td>
					<td class="num">${money(row.regular_price, row.currency)}</td>
					<td class="num"><b>${money(row.selling_price, row.currency)}</b>${row.is_promotional ? `<br><span class="indicator-pill red">${__("Акція")}</span>` : ""}</td>
					<td>${row.last_printed_at ? escape(frappe.datetime.str_to_user(row.last_printed_at)) : "—"}</td>
					<td><input class="price-tag-copies form-control input-xs" type="number" min="1" step="1" value="1" ${disabled ? "disabled" : ""}></td>
				</tr>
			`;
		}).join("");
		result.html(`
			<div class="table-responsive"><table class="price-tag-stock-table">
				<thead><tr><th><input class="price-tag-select-all" type="checkbox" checked></th><th>${__("Товар / штрихкод")}</th><th>${__("Назва")}</th><th class="num">${__("Залишок")}</th><th class="num">${__("Звичайна ціна")}</th><th class="num">${__("Ціна на ціннику")}</th><th>${__("Останній друк")}</th><th>${__("Копій")}</th></tr></thead>
				<tbody>${body}</tbody>
			</table></div>
		`);
		result.find(".price-tag-select-all").on("change", function () {
			result.find(".price-tag-select:not(:disabled)").prop("checked", this.checked);
		});
	}

	function refresh() {
		if (!warehouse.get_value() || !price_list.get_value()) return;
		frappe.call({
			method: "erpnext_ua.ua_price_tags.service.get_stock_items",
			args: {
				warehouse: warehouse.get_value(),
				price_list: price_list.get_value(),
				promotional_price_list: promotional_price_list.get_value() || "",
				brand: brand.get_value(),
				item_group: item_group.get_value(),
				search_text: search_text.get_value(),
			},
			freeze: true,
			freeze_message: __("Завантажуємо залишки та ціни…"),
			callback: (response) => render(response.message || []),
		});
	}

	page.set_primary_action(__("Оновити"), refresh, "refresh");
	page.add_inner_button(__("Створити пакет друку"), () => {
		const items = [];
		result.find("tbody tr").each(function () {
			const tr = $(this);
			if (!tr.find(".price-tag-select").prop("checked")) return;
			const row = state.rows[Number(tr.data("index"))];
			items.push({item_code: row.item_code, uom: row.uom, copies: Number(tr.find(".price-tag-copies").val()) || 1});
		});
		if (!items.length) return frappe.msgprint(__("Оберіть хоча б один товар."));
		frappe.call({
			method: "erpnext_ua.ua_price_tags.service.create_item_jobs",
			args: {
				items,
				warehouse: warehouse.get_value(),
				price_list: price_list.get_value(),
				promotional_price_list: promotional_price_list.get_value() || "",
				template_mode: template_mode.get_value(),
			},
			freeze: true,
			freeze_message: __("Створюємо пакети друку…"),
			callback(response) {
				const jobs = response.message || [];
				if (jobs.length > 1) frappe.show_alert(__("Створено пакетів: {0}", [jobs.length]));
				if (jobs.length) frappe.set_route("Form", "Price Tag Print Job", jobs[0]);
			},
		});
	});
	search_text.$input.on("keydown", (event) => {if (event.key === "Enter") refresh();});

	frappe.call({
		method: "erpnext_ua.ua_price_tags.service.get_configuration",
		callback(response) {
			const config = response.message || {};
			price_list.set_value(config.price_list);
			promotional_price_list.set_value(config.promotional_price_list);
		},
	});
};
