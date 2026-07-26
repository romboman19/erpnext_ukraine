// Майстер показує стан налаштування і не зберігає жодних власних даних:
// це Single DocType (один рядок на весь сайт), тому будь-яке збережене тут
// поле лишалося б на екрані для наступної компанії, яку тут переглядатимуть.
//
// Кроки, для яких вже є повноцінний DocType (FOP Profile, UA Chart of
// Accounts Setup, PRRO Cash Register), ведуть саме туди — з передзаповненою
// компанією/ФОП, без дублювання полів. Лише "Каса" лишається діалогом: він
// заразом створює роздрібного покупця, чого звичайна форма POS Cash Desk не
// робить.

const STATUS_INDICATOR = {
	done: "green",
	pending: "orange",
	blocked: "red",
};

const SEVERITY_LABEL = {
	required: "обов'язково",
	fiscal: "для фіскальних чеків",
	recommended: "рекомендовано",
};

// fix_action -> куди вести. Кожен запис або відкриває нову форму
// (route: doctype, args: {}), або є суто клієнтською дією без сервера.
const NAVIGATE_ACTIONS = {
	apply_fop_profile: {
		doctype: "FOP Profile",
		args: (frm) => ({ company: frm.doc.company }),
	},
	apply_chart: {
		doctype: "UA Chart of Accounts Setup",
		args: (frm) => ({ company: frm.doc.company }),
	},
	apply_prro_register: {
		doctype: "PRRO Cash Register",
		args: async (frm) => ({
			fop_profile: await frappe.db.get_value("FOP Profile", { company: frm.doc.company }, "name")
				.then((r) => r.message && r.message.name),
		}),
	},
};

frappe.ui.form.on("UA Setup Wizard", {
	refresh(frm) {
		frm.disable_save();
		render_readiness(frm);
	},

	company(frm) {
		render_readiness(frm);
	},
});

function render_readiness(frm) {
	frappe.call({
		method: "erpnext_ua.ua_setup.service.readiness",
		args: { company: frm.doc.company },
		callback: ({ message }) => {
			if (!message) return;
			draw(frm, message);
		},
	});
}

function draw(frm, report) {
	const wrapper = frm.get_field("readiness_html").$wrapper;
	wrapper.empty();

	const summary = report.can_fiscalize
		? __("Система готова до роботи, включно з фіскальними чеками.")
		: report.can_sell
			? __("Продажі можливі. Для фіскальних чеків залишилися кроки ПРРО.")
			: __("Ще не готово: закрийте обов'язкові кроки нижче.");

	const colour = report.can_fiscalize ? "green" : report.can_sell ? "orange" : "red";
	wrapper.append(`<p><span class="indicator ${colour}">${frappe.utils.escape_html(summary)}</span></p>`);

	if (report.enabled_connectors.length) {
		const names = report.enabled_connectors.map(frappe.utils.escape_html).join(", ");
		wrapper.append(`<p class="text-muted">${__("Увімкнені конектори")}: ${names}</p>`);
	}

	const list = $('<div class="ua-setup-steps"></div>').appendTo(wrapper);
	for (const check of report.checks) {
		const row = $(`
			<div class="ua-setup-step" style="padding:6px 0;border-bottom:1px solid var(--border-color)">
				<span class="indicator ${STATUS_INDICATOR[check.status] || "gray"}">
					${frappe.utils.escape_html(check.title)}
				</span>
				<span class="text-muted small"> — ${frappe.utils.escape_html(SEVERITY_LABEL[check.severity] || "")}</span>
				${check.detail ? `<div class="text-muted small">${frappe.utils.escape_html(check.detail)}</div>` : ""}
			</div>
		`).appendTo(list);

		if (check.status !== "done" && check.fix_action) {
			$(`<button class="btn btn-xs btn-default" style="margin-top:4px">${__("Виконати крок")}</button>`)
				.appendTo(row)
				.on("click", () => run_step(frm, check));
		}
	}

	frm.get_field("readiness_html").refresh();
}

async function run_step(frm, check) {
	if (!frm.doc.company) {
		frappe.msgprint(__("Спершу оберіть компанію."));
		return;
	}

	const navigate = NAVIGATE_ACTIONS[check.fix_action];
	if (navigate) {
		const args = await navigate.args(frm);
		frappe.new_doc(navigate.doctype, args);
		return;
	}

	if (check.fix_action === "apply_cash_desk") {
		open_cash_desk_dialog(frm, check);
		return;
	}

	// Кроки без додаткових даних: apply_language, apply_tax_parameters,
	// apply_payment_methods. Компанія в них не потрібна, окрім факту, що вона
	// обрана (перевірено вище).
	call_step(frm, check, {});
}

function open_cash_desk_dialog(frm, check) {
	const dialog = new frappe.ui.Dialog({
		title: __("Каса"),
		fields: [
			{
				fieldname: "warehouse",
				fieldtype: "Link",
				label: __("Склад каси"),
				options: "Warehouse",
				get_query: () => ({ filters: { company: frm.doc.company, is_group: 0, disabled: 0 } }),
			},
			{
				fieldname: "desk_name",
				fieldtype: "Data",
				label: __("Назва каси"),
				default: "Каса 1",
				reqd: 1,
			},
		],
		primary_action_label: __("Створити"),
		primary_action: (values) => {
			dialog.hide();
			call_step(frm, check, { company: frm.doc.company, ...values });
		},
	});
	dialog.show();
}

function call_step(frm, check, args) {
	frm.call({
		doc: frm.doc,
		method: "run_step",
		args: { step: check.fix_action, args },
		freeze: true,
		freeze_message: __("Виконується крок: {0}", [check.title]),
	}).then(() => {
		frappe.show_alert({ message: __("Крок виконано: {0}", [check.title]), indicator: "green" });
		render_readiness(frm);
	});
}
