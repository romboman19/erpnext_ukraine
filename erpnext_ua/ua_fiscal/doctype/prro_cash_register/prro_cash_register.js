// Команда "Objects" фіскального сервера повертає всі господарські одиниці й
// каси ПРРО, зареєстровані для підписанта КЕП — незалежно від того, яку саме
// касу зараз створюють, тому завжди показуємо перелік для вибору, а не
// вгадуємо єдиний правильний варіант.

frappe.ui.form.on("PRRO Cash Register", {
	refresh(frm) {
		frm.add_custom_button(__("Завантажити з ДПС"), () => load_from_dps(frm), __("ДПС"));
	},
});

async function load_from_dps(frm) {
	if (!frm.doc.default_kep_key) {
		frappe.msgprint(__("Спершу оберіть КЕП у секції «Підпис»."));
		return;
	}

	const response = await frappe.call({
		method: "erpnext_ua.ua_fiscal.registrar_lookup.list_registered_objects",
		args: { kep_key: frm.doc.default_kep_key },
		freeze: true,
		freeze_message: __("Отримання переліку об'єктів із ДПС…"),
	});
	const rows = response.message || [];
	if (rows.length === 1) {
		apply_row(frm, rows[0]);
		return;
	}
	show_picker(frm, rows);
}

function show_picker(frm, rows) {
	const escape = frappe.utils.escape_html;
	const options = rows
		.map((row, i) => `${i}:::${escape(row.unit_name)} — ${escape(row.register_name)} (ФН ${escape(row.fiscal_number)})`)
		.join("\n");

	const dialog = new frappe.ui.Dialog({
		title: __("Оберіть касу ПРРО"),
		fields: [
			{
				fieldname: "selection",
				fieldtype: "Select",
				label: __("Зареєстровано в ДПС"),
				options,
				reqd: 1,
			},
		],
		primary_action_label: __("Заповнити"),
		primary_action: (values) => {
			const index = Number(values.selection.split(":::")[0]);
			apply_row(frm, rows[index]);
			dialog.hide();
		},
	});
	dialog.show();
}

function apply_row(frm, row) {
	frm.set_value({
		unit_name: row.unit_name,
		unit_address: row.unit_address,
		fiscal_number: row.fiscal_number,
		register_local_number: row.register_local_number,
	});
	if (!frm.doc.register_name) {
		frm.set_value("register_name", row.register_name);
	}
	frappe.show_alert({ message: __("Дані заповнено з ДПС"), indicator: "green" });
}
