frappe.ui.form.on("FOP Profile", {
	refresh(frm) {
		frm.set_query("cabinet_kep_key", () => ({ filters: { status: "Active" } }));
		set_dps_managed_fields(frm);
		frm.add_custom_button(
			__("Завантажити дані з ДПС"),
			() => load_taxpayer_card(frm),
			__("ДПС")
		);

		if (frm.is_new()) return;

		frm.add_custom_button(__("Згенерувати податковий календар"), () => {
			frappe.prompt(
				{
					fieldname: "year",
					fieldtype: "Int",
					label: __("Рік"),
					default: new Date().getFullYear(),
					reqd: 1,
				},
				(values) => {
					frappe
						.call("erpnext_ua.ua_fop.tax_calendar.generate_deadlines", {
							fop_profile: frm.doc.name,
							year: values.year,
						})
						.then((r) => {
							const m = r.message;
							frappe.msgprint(
								__("Календар на {0}: створено {1}, вже існувало {2}", [
									m.year,
									m.created,
									m.skipped,
								])
							);
						});
				},
				__("Податковий календар")
			);
		});

		frm.add_custom_button(__("Дедлайни"), () => {
			frappe.set_route("List", "UA Tax Deadline", { company: frm.doc.company });
		});

		render_headline(frm);
	},

	allow_manual_dps_fields(frm) {
		set_dps_managed_fields(frm);
	},
});

const DPS_FIELD_LABELS = {
	fop_full_name: "ПІБ підприємця",
	prro_registered_name: "Найменування для ПРРО",
	tax_id: "РНОКПП",
	status: "Статус",
	single_tax_registration_date: "Дата реєстрації ЄП",
	single_tax_group: "Група ЄП",
	tax_rate_mode: "Режим ставки",
	vat_payer: "Платник ПДВ",
	vat_number: "ІПН платника ПДВ",
	iban: "IBAN",
	bank_name: "Банк",
	kved_main: "Основний КВЕД",
	registration_address: "Податкова адреса",
};

function set_dps_managed_fields(frm) {
	const read_only = !frm.doc.allow_manual_dps_fields;
	["fop_full_name", "prro_registered_name"].forEach((fieldname) => {
		frm.set_df_property(fieldname, "read_only", read_only ? 1 : 0);
	});
}

function escaped(value) {
	return frappe.utils.escape_html(String(value ?? ""));
}

function preview_html(frm, result) {
	const rows = Object.entries(result.updates || {})
		.map(([fieldname, value]) => {
			const current = frm.doc[fieldname] ?? "";
			return `<tr>
				<td><b>${escaped(__(DPS_FIELD_LABELS[fieldname] || fieldname))}</b></td>
				<td>${escaped(current)}</td>
				<td>${escaped(value)}</td>
			</tr>`;
		})
		.join("");
	const accounts = (result.bank_accounts || [])
		.map((item) => `<li>${escaped(item.iban)} · ${escaped(item.bank_name)} · ${escaped(item.currency)}</li>`)
		.join("");
	const kveds = (result.kveds || [])
		.map((item) => `<li>${item.is_main ? "<b>Основний:</b> " : ""}${escaped(item.code)} — ${escaped(item.title)}</li>`)
		.join("");
	return `<div class="small">
		<table class="table table-bordered">
			<thead><tr><th>${escaped(__("Поле"))}</th><th>${escaped(__("Зараз"))}</th><th>${escaped(__("ДПС"))}</th></tr></thead>
			<tbody>${rows}</tbody>
		</table>
		${accounts ? `<b>${escaped(__("Рахунки"))}</b><ul>${accounts}</ul>` : ""}
		${kveds ? `<b>${escaped(__("КВЕДи"))}</b><ul>${kveds}</ul>` : ""}
	</div>`;
}

async function apply_draft_result(frm, result) {
	await frm.set_value(result.updates || {});
	const kveds = result.kveds || [];
	if (kveds.length) {
		const main = kveds.find((item) => item.is_main) || kveds[0];
		await frm.set_value("kved_main", main.code);
		frm.clear_table("kveds");
		kveds.filter((item) => item.code !== main.code).forEach((item) => {
			const row = frm.add_child("kveds");
			row.kved = item.code;
			row.title = item.title || item.code;
		});
		frm.refresh_field("kveds");
	}
	frm.dirty();
}

async function load_taxpayer_card(frm) {
	if (!frm.doc.tax_id || !frm.doc.cabinet_kep_key) {
		frappe.msgprint(__("Спочатку вкажіть РНОКПП і виберіть КЕП для кабінету ДПС."));
		return;
	}
	const preview = await frappe.call({
		method: "erpnext_ua.ua_fop.taxpayer_cabinet.preview_taxpayer_card",
		args: { tax_id: frm.doc.tax_id, kep_key: frm.doc.cabinet_kep_key },
		freeze: true,
		freeze_message: __("Отримання облікових даних із ДПС…"),
	});
	const result = preview.message || {};
	const accounts = result.bank_accounts || [];
	const dialog = new frappe.ui.Dialog({
		title: __("Дані ФОП із кабінету ДПС"),
		fields: [
			{ fieldname: "preview", fieldtype: "HTML", options: preview_html(frm, result) },
			{
				fieldname: "selected_iban",
				fieldtype: "Select",
				label: __("Основний IBAN"),
				options: accounts.map((item) => item.iban).join("\n"),
				default: result.updates && result.updates.iban,
				hidden: accounts.length === 0,
				reqd: accounts.length > 0,
			},
		],
		primary_action_label: frm.is_new() ? __("Заповнити форму") : __("Оновити профіль"),
		primary_action: async (values) => {
			dialog.disable_primary_action();
			try {
				if (frm.is_new()) {
					const prepared = await frappe.call({
						method: "erpnext_ua.ua_fop.taxpayer_cabinet.prepare_fop_profile",
						args: {
							tax_id: frm.doc.tax_id,
							kep_key: frm.doc.cabinet_kep_key,
							selected_iban: values.selected_iban || null,
						},
						freeze: true,
					});
					await apply_draft_result(frm, prepared.message || {});
					frappe.show_alert({ message: __("Дані заповнено. Перевірте та збережіть профіль."), indicator: "green" });
				} else {
					await frappe.call({
						method: "erpnext_ua.ua_fop.taxpayer_cabinet.sync_fop_profile",
						args: { fop_profile: frm.doc.name, selected_iban: values.selected_iban || null },
						freeze: true,
					});
					await frm.reload_doc();
					frappe.show_alert({ message: __("Профіль ФОП синхронізовано з ДПС"), indicator: "green" });
				}
				dialog.hide();
			} finally {
				dialog.enable_primary_action();
			}
		},
	});
	dialog.show();
}

function render_headline(frm) {
	const fmt = (v) => format_currency(v, "UAH");
	Promise.all([
		frm.call("get_current_tax_parameters"),
		frappe.call("erpnext_ua.ua_fop.income_monitor.get_income_summary", {
			fop_profile: frm.doc.name,
		}),
	]).then(([params_r, income_r]) => {
		const parts = [];
		const p = params_r.message;
		const inc = income_r.message;

		if (inc && inc.income_limit) {
			const pct = inc.limit_used_percent;
			const color = pct >= 95 ? "red" : pct >= 80 ? "orange" : "green";
			parts.push(
				`Дохід ${inc.year}: <b style="color:${color}">${fmt(inc.income)}</b> ` +
					`з ${fmt(inc.income_limit)} (<b style="color:${color}">${pct}%</b> ліміту)`
			);
		}
		if (p) {
			if (p.single_tax_monthly) parts.push(`ЄП: <b>${fmt(p.single_tax_monthly)}/міс</b>`);
			if (p.single_tax_percent_no_vat && frm.doc.tax_rate_mode === "5% без ПДВ")
				parts.push(`ЄП: <b>${p.single_tax_percent_no_vat}%</b>`);
			if (p.single_tax_percent_vat && frm.doc.tax_rate_mode === "3% з ПДВ")
				parts.push(`ЄП: <b>${p.single_tax_percent_vat}% + ПДВ</b>`);
			if (p.military_levy_monthly) parts.push(`ВЗ: <b>${fmt(p.military_levy_monthly)}/міс</b>`);
			if (p.military_levy_percent) parts.push(`ВЗ: <b>${p.military_levy_percent}%</b>`);
			if (p.esv_monthly) parts.push(`ЄСВ: <b>${fmt(p.esv_monthly)}/міс</b>`);
		}
		if (parts.length) frm.dashboard.set_headline(parts.join(" · "));
	});
}
