(() => {
	const MULTIPLIER = 1.2;
	const LEGACY_TEMPLATE_PREFIXES = [
		"Додати ПДВ 20% до ціни (неплатник)",
		"Ціна вже містить ПДВ 20% (неплатник)",
	];
	const updatingRows = new Set();

	function is_enabled(frm) {
		return Boolean(cint(frm.doc.ua_add_vat_20_to_prices));
	}

	function round_price(value) {
		return flt(value, 2);
	}

	async function set_row_price(frm, row, netPrice) {
		if (!row || updatingRows.has(row.name)) return;
		updatingRows.add(row.name);
		try {
			const grossPrice = round_price(flt(netPrice) * MULTIPLIER);
			await frappe.model.set_value(row.doctype, row.name, {
				ua_price_without_vat: round_price(netPrice),
				price_list_rate: grossPrice,
				rate: grossPrice,
				discount_percentage: 0,
				discount_amount: 0,
			});
		} finally {
			updatingRows.delete(row.name);
		}
		frm.refresh_field("items");
	}

	function remove_legacy_vat_rows(frm) {
		const template = String(frm.doc.taxes_and_charges || "");
		const legacyTemplate = LEGACY_TEMPLATE_PREFIXES.some((prefix) => template.startsWith(prefix));
		if (legacyTemplate) frm.doc.taxes_and_charges = "";
		frm.doc.taxes = (frm.doc.taxes || []).filter((row) => {
			const description = String(row.description || "");
			return !legacyTemplate && !description.startsWith("Невідшкодовуваний ПДВ 20%");
		});
		frm.refresh_field("taxes_and_charges");
		frm.refresh_field("taxes");
	}

	function update_grid(frm) {
		const enabled = is_enabled(frm);
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		if (!grid) return;
		grid.update_docfield_property("ua_price_without_vat", "hidden", enabled ? 0 : 1);
		grid.update_docfield_property("rate", "read_only", enabled ? 1 : 0);
		grid.update_docfield_property("price_list_rate", "read_only", enabled ? 1 : 0);
		frm.refresh_field("items");
	}

	async function toggle_vat(frm) {
		if (frm.doc.docstatus !== 0) return;
		if (is_enabled(frm)) {
			remove_legacy_vat_rows(frm);
			for (const row of frm.doc.items || []) {
				const currentNet = flt(row.ua_price_without_vat);
				const netPrice = currentNet || flt(row.rate);
				await set_row_price(frm, row, netPrice);
			}
			frappe.show_alert({
				message: __("Вводьте ціну постачальника у колонці «Ціна без ПДВ»; Rate буде +20%."),
				indicator: "blue",
			});
		} else {
			for (const row of frm.doc.items || []) {
				if (row.ua_price_without_vat === null || row.ua_price_without_vat === undefined) continue;
				const netPrice = round_price(row.ua_price_without_vat);
				await frappe.model.set_value(row.doctype, row.name, {
					price_list_rate: netPrice,
					rate: netPrice,
					discount_percentage: 0,
					discount_amount: 0,
				});
			}
		}
		update_grid(frm);
		frm.dirty();
	}

	async function update_net_price(frm, cdt, cdn) {
		if (!is_enabled(frm)) return;
		const row = locals[cdt][cdn];
		if (!row || updatingRows.has(row.name)) return;
		await set_row_price(frm, row, row.ua_price_without_vat);
	}

	function capture_loaded_rate(frm, cdt, cdn) {
		if (!is_enabled(frm)) return;
		frappe.after_ajax(async () => {
			const row = locals[cdt] && locals[cdt][cdn];
			if (!row || flt(row.ua_price_without_vat) || !flt(row.rate)) return;
			await set_row_price(frm, row, row.rate);
		});
	}

	for (const doctype of ["Purchase Receipt", "Purchase Invoice"]) {
		frappe.ui.form.on(doctype, {
			refresh: update_grid,
			ua_add_vat_20_to_prices: toggle_vat,
		});
	}

	for (const itemDoctype of ["Purchase Receipt Item", "Purchase Invoice Item"]) {
		frappe.ui.form.on(itemDoctype, {
			ua_price_without_vat: update_net_price,
			item_code: capture_loaded_rate,
		});
	}
})();
