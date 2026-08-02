frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.ua_fulfillment_physical_location || frm.doc.ua_sale_fulfillment) {
			return;
		}
		frm.add_custom_button(__("Fulfill via Global FIFO"), async () => {
			const response = await frappe.call({
				method: "erpnext_ua.group_stock_fifo.services.fulfillment_channels.fulfill_sales_order",
				args: { sales_order: frm.doc.name },
				freeze: true,
				freeze_message: __("Allocating global FIFO stock..."),
			});
			await frm.reload_doc();
			const invoices = response.message.sales_invoices || [];
			if (invoices.length) {
				frappe.set_route("Form", "Sales Invoice", invoices[0]);
			}
		}, __("Create"));
	},
});
