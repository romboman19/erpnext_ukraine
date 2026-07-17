frappe.ui.form.on("UA Chart of Accounts Setup", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Перевірити"), async () => {
			const response = await frm.call("preview");
			show_result(response.message);
			await frm.reload_doc();
		});

		if (frm.doc.status !== "Applied") {
			frm.add_custom_button(
				__("Застосувати план"),
				() => {
					frappe.confirm(
						__("Поточне дерево рахунків буде замінене. Продовжити?"),
						async () => {
							const response = await frm.call("apply_template");
							show_result(response.message);
							await frm.reload_doc();
						}
					);
				},
				__("Дії")
			);
		}
	},
});

function show_result(result) {
	if (!result) return;
	const escape = frappe.utils.escape_html;
	const blockers = (result.blockers || []).map((row) => `<li>${escape(row)}</li>`).join("");
	const warnings = (result.warnings || []).map((row) => `<li>${escape(row)}</li>`).join("");
	const template = result.template || {};
	frappe.msgprint({
		title: result.allowed === false ? __("Застосування заблоковано") : __("Український план рахунків"),
		indicator: result.allowed === false ? "red" : "green",
		message: `
			<p><b>${escape(template.title || "")}</b></p>
			<p>${__("Рахунків у шаблоні")}: ${template.account_count || result.created_account_count || 0}</p>
			${blockers ? `<p><b>${__("Блокери")}</b></p><ul>${blockers}</ul>` : ""}
			${warnings ? `<p><b>${__("Зауваження")}</b></p><ul>${warnings}</ul>` : ""}
		`,
	});
}
