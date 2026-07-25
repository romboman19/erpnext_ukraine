frappe.ui.form.on("PRRO Receipt", {
	refresh(frm) {
		const printable =
			!frm.is_new() &&
			["Fiscalized", "Offline"].includes(frm.doc.status) &&
			["Sale", "Return", "Storno", "Open Shift", "Z Report"].includes(frm.doc.receipt_kind);

		if (!printable) return;

		frm.add_custom_button(__("Відкрити чек"), () => open_fiscal_receipt(frm, false), __("Чек"));
		frm.add_custom_button(__("Друкувати чек"), () => open_fiscal_receipt(frm, true), __("Чек"));
	},
});

function escape_html(value) {
	return $("<div>").text(value || "").html();
}

async function fiscal_receipt_preview(name) {
	const result = await frappe.call({
		method: "erpnext_ua.ua_fiscal.doctype.prro_receipt.prro_receipt.receipt_preview",
		args: { name },
		freeze: true,
		freeze_message: __("Формуємо чек…"),
	});
	return result.message;
}

function fiscal_receipt_document(title, body, auto_print = false) {
	return `<!doctype html><html lang="uk"><head><meta charset="utf-8"><title>${escape_html(title)}</title><style>
		@page{size:80mm auto;margin:4mm}body{width:72mm;margin:0 auto;font:12px/1.35 monospace;color:#000}
		.fiscal-center{text-align:center}.fiscal-muted{color:#555;font-size:10px}.fiscal-table{width:100%;border-collapse:collapse;margin:8px 0}
		.fiscal-table td{padding:3px 0;border-bottom:1px dotted #777;vertical-align:top}.fiscal-table td:last-child{text-align:right;white-space:nowrap}
		.fiscal-qr img{width:190px;height:190px}.fiscal-url{overflow-wrap:anywhere}.fiscal-barcode img{display:block;width:100%;max-width:460px;height:auto;margin:5px auto;image-rendering:pixelated}.fiscal-rule{border-top:1px dashed #000;margin:8px 0}.fiscal-title{font-size:18px}.print-action{width:100%;margin-top:12px;padding:9px}
		@media print{.print-action{display:none}}
	</style></head><body>${body}<button class="print-action" onclick="window.print()">Друкувати</button>${
		auto_print ? "<script>window.addEventListener('load',()=>window.print())<\\/script>" : ""
	}</body></html>`;
}

async function open_fiscal_receipt(frm, print_immediately) {
	const print_window = print_immediately ? window.open("", "_blank", "width=520,height=760") : null;
	try {
		const data = await fiscal_receipt_preview(frm.doc.name);
		const title = `${__(data.title || "Фіскальний чек")} № ${data.fiscal_number || frm.doc.fiscal_number || frm.doc.name}`;
		if (print_immediately) {
			if (!print_window) {
				frappe.msgprint(__("Браузер заблокував вікно друку."));
				return;
			}
			print_window.document.write(fiscal_receipt_document(title, data.html, true));
			print_window.document.close();
			print_window.focus();
			return;
		}

		const dialog = new frappe.ui.Dialog({
			title,
			size: "large",
			fields: [
				{
					fieldname: "receipt",
					fieldtype: "HTML",
					options: `<style>
						.prro-preview{max-width:430px;margin:0 auto;font:13px/1.4 monospace;color:#111}
						.prro-preview .fiscal-center{text-align:center}.prro-preview .fiscal-muted{color:#666;font-size:11px}
						.prro-preview .fiscal-table{width:100%;border-collapse:collapse;margin:10px 0}
						.prro-preview .fiscal-table td{padding:4px 0;border-bottom:1px dotted #aaa;vertical-align:top}
						.prro-preview .fiscal-table td:last-child{text-align:right;white-space:nowrap}
						.prro-preview .fiscal-qr img{width:190px;height:190px}.prro-preview .fiscal-url{overflow-wrap:anywhere}
						.prro-preview .fiscal-barcode img{display:block;width:100%;max-width:460px;height:auto;margin:5px auto;image-rendering:pixelated}
						.prro-preview .fiscal-rule{border-top:1px dashed #777;margin:10px 0}.prro-preview .fiscal-title{font-size:18px}
					</style><div class="prro-preview">${data.html}</div>`,
				},
			],
			primary_action_label: __("Друкувати"),
			primary_action() {
				const win = window.open("", "_blank", "width=520,height=760");
				if (!win) {
					frappe.msgprint(__("Браузер заблокував вікно друку."));
					return;
				}
				win.document.write(fiscal_receipt_document(title, data.html, true));
				win.document.close();
				win.focus();
			},
		});
		dialog.show();
	} catch (error) {
		if (print_window) print_window.close();
		throw error;
	}
}
