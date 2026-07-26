frappe.ui.form.on("LiqPay Settings", {
  refresh(frm) {
    frm.add_custom_button("✅ Перевірити профілі", async () => {
      const r = await frappe.call({
        method: "ukrainian_integrations.payments.liqpay.service.liqpay_list_profiles",
      });
      const m = r.message || {};
      frappe.msgprint(`Профілів: ${m.count || 0}`);
    });
  },
});
