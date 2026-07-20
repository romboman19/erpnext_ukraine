frappe.ui.form.on("Price Tag Print Job", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Підготувати до друку"), () => {
				frm.call("mark_ready").then(() => frm.reload_doc());
			});
		}

		if (["Ready", "Printed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Відкрити друк"), () => {
				const query = new URLSearchParams({
					doctype: frm.doc.doctype,
					name: frm.doc.name,
					format: frm.doc.print_format,
					no_letterhead: "1",
				});
				window.open(`/printview?${query.toString()}`, "_blank", "noopener");
			}, __("Друк"));
		}

		if (frm.doc.status === "Ready") {
			frm.add_custom_button(__("Позначити надрукованим"), () => {
				frappe.confirm(__("Цінники фізично надруковано?"), () => {
					frm.call("mark_printed").then(() => frm.reload_doc());
				});
			}, __("Друк"));
		}

		if (["Ready", "Printed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Повторити цей знімок"), () => {
				frappe.call({
					method: "erpnext_ua.ua_price_tags.service.repeat_print_job",
					args: {job_name: frm.doc.name},
					freeze: true,
					callback: (response) => frappe.set_route("Form", "Price Tag Print Job", response.message),
				});
			}, __("Створити"));
		}
	},
});
