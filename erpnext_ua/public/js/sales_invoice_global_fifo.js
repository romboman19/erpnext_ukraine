frappe.ui.form.on("Sales Invoice", {
	refresh(frm) {
		if (
			frm.doc.docstatus !== 0 ||
			!frm.doc.ua_fulfillment_physical_location ||
			frm.doc.gsf_managed_sale ||
			frm.doc.cc_managed_sale
		) {
			return;
		}
		frm.add_custom_button(__("Fulfill via Global FIFO"), async () => {
			await frm.save();
			const response = await frappe.call({
				method: "erpnext_ua.group_stock_fifo.services.fulfillment_channels.fulfill_sales_invoice",
				args: { draft_invoice: frm.doc.name },
				freeze: true,
				freeze_message: __("Reserving global stock and posting legal sale routes…"),
			});
			await frm.reload_doc();
			const invoices = response.message.sales_invoices || [];
			frappe.msgprint({
				title: __("Sale Fulfilled"),
				message: __("Created Sales Invoices: {0}", [invoices.join(", ")]),
				indicator: "green",
			});
		}, __("Global FIFO"));
	},
});
