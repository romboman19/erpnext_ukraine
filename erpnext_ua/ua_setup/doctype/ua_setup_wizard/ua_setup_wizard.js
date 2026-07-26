// Майстер показує стан налаштування і дає запустити лише ті кроки, які ще не
// закриті. Кожен крок ідемпотентний, тому повторний запуск нічого не зламає.

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

function run_step(frm, check) {
	const run = () =>
		frm.call({
			doc: frm.doc,
			method: "run_step",
			args: { step: check.fix_action },
			freeze: true,
			freeze_message: __("Виконується крок: {0}", [check.title]),
		}).then(() => {
			frappe.show_alert({ message: __("Крок виконано: {0}", [check.title]), indicator: "green" });
			frm.reload_doc().then(() => render_readiness(frm));
		});

	// Заміна плану рахунків видаляє наявні рахунки компанії. Це єдиний
	// незворотний крок майстра, тому він питає окремо.
	if (check.fix_action !== "apply_chart") {
		run();
		return;
	}

	if (!frm.doc.chart_template) {
		frappe.msgprint(__("Спершу оберіть шаблон плану рахунків у полі вище."));
		frm.scroll_to_field("chart_template");
		return;
	}

	frappe.call({
		method: "erpnext_ua.ua_setup.service.chart_preflight",
		args: { company: frm.doc.company, chart_template: frm.doc.chart_template },
		callback: ({ message }) => {
			if (!message) return;
			const blockers = (message.blockers || []).map(frappe.utils.escape_html);
			if (blockers.length) {
				frappe.msgprint({
					title: __("Заміна плану рахунків неможлива"),
					message: `<ul><li>${blockers.join("</li><li>")}</li></ul>`,
					indicator: "red",
				});
				return;
			}
			const warnings = (message.warnings || []).map(frappe.utils.escape_html);
			frappe.confirm(
				`${__("План рахунків компанії {0} буде замінено.", [frappe.utils.escape_html(frm.doc.company)])}
				 <ul><li>${warnings.join("</li><li>")}</li></ul>`,
				run
			);
		},
	});
}
