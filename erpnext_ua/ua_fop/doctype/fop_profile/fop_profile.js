frappe.ui.form.on("FOP Profile", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.call("get_current_tax_parameters").then((r) => {
			if (!r.message) {
				frm.dashboard.set_headline(
					__("Немає довідкових податкових параметрів на поточний рік для групи {0}", [
						frm.doc.single_tax_group,
					])
				);
				return;
			}
			const p = r.message;
			const fmt = (v) => format_currency(v, "UAH");
			let parts = [`Ліміт доходу: <b>${fmt(p.income_limit)}</b>`];
			if (p.single_tax_monthly) parts.push(`ЄП: <b>${fmt(p.single_tax_monthly)}/міс</b>`);
			if (p.single_tax_percent_no_vat && frm.doc.tax_rate_mode === "5% без ПДВ")
				parts.push(`ЄП: <b>${p.single_tax_percent_no_vat}%</b>`);
			if (p.single_tax_percent_vat && frm.doc.tax_rate_mode === "3% з ПДВ")
				parts.push(`ЄП: <b>${p.single_tax_percent_vat}% + ПДВ</b>`);
			if (p.military_levy_monthly) parts.push(`ВЗ: <b>${fmt(p.military_levy_monthly)}/міс</b>`);
			if (p.military_levy_percent) parts.push(`ВЗ: <b>${p.military_levy_percent}%</b>`);
			if (p.esv_monthly) parts.push(`ЄСВ: <b>${fmt(p.esv_monthly)}/міс</b>`);
			frm.dashboard.set_headline(parts.join(" · "));
		});
	},
});
