frappe.ui.form.on("PB POS Terminal", {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button("🟢 Тест зв'язку", async () => {
      try {
        const r = await frappe.call({
          method: "ukrainian_integrations.payments.privat_pos.service.pb_pos_test_connection",
          args: { terminal: frm.doc.name },
        });
        frappe.msgprint({ title: "OK", indicator: "green", message: "Зв'язок із шлюзом/терміналом успішний" });
      } catch (e) {
        frappe.msgprint({ title: "Помилка зв'язку", indicator: "red", message: (e && e.message) || "Connection failed" });
      }
    });

    frm.add_custom_button("💳 Тест оплати", async () => {
      const d = new frappe.ui.Dialog({
        title: "Тест оплати",
        fields: [{ fieldname: "amount", label: "Сума", fieldtype: "Float", reqd: 1, default: 1 }],
        primary_action_label: "Запустити",
        primary_action: async (v) => {
          try {
            const r = await frappe.call({
              method: "ukrainian_integrations.payments.privat_pos.service.pb_pos_test_payment",
              args: { terminal: frm.doc.name, amount: v.amount },
            });
            frappe.msgprint({ title: "Успіх", indicator: "green", message: "Тест оплати відправлено" });
            d.hide();
          } catch (e) {
            frappe.msgprint({ title: "Помилка оплати", indicator: "red", message: (e && e.message) || "Sale failed" });
          }
        },
      });
      d.show();
    });

    frm.add_custom_button("↩️ Тест повернення", async () => {
      const d = new frappe.ui.Dialog({
        title: "Тест повернення",
        fields: [
          { fieldname: "amount", label: "Сума", fieldtype: "Float", reqd: 1, default: 1 },
          { fieldname: "reference_operation_id", label: "Reference Operation ID", fieldtype: "Data" },
        ],
        primary_action_label: "Запустити",
        primary_action: async (v) => {
          try {
            await frappe.call({
              method: "ukrainian_integrations.payments.privat_pos.service.pb_pos_test_refund",
              args: { terminal: frm.doc.name, amount: v.amount, reference_operation_id: v.reference_operation_id || null },
            });
            frappe.msgprint({ title: "Успіх", indicator: "green", message: "Тест повернення відправлено" });
            d.hide();
          } catch (e) {
            frappe.msgprint({ title: "Помилка повернення", indicator: "red", message: (e && e.message) || "Refund failed" });
          }
        },
      });
      d.show();
    });
  },
});
