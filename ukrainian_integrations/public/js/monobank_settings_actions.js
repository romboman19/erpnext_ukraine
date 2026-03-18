frappe.ui.form.on("Monobank Settings", {
  refresh(frm) {
    frm.add_custom_button("🔄 Завантажити рахунки Monobank", async () => {
      try {
        const r = await frappe.call({
          method: "ukrainian_integrations.payments.monobank.service.mono_list_accounts",
        });
        const accounts = (r.message && r.message.accounts) || [];
        if (!accounts.length) {
          frappe.msgprint("Не знайдено рахунків Monobank");
          return;
        }

        const options = accounts.map((a) => a.label).join("
");
        const d = new frappe.ui.Dialog({
          title: "Вибір рахунку Monobank",
          fields: [
            { fieldname: "mono_account", label: "Monobank Account", fieldtype: "Select", options, reqd: 1 },
            { fieldname: "bank_account", label: "ERP Bank Account", fieldtype: "Link", options: "Bank Account" },
          ],
          primary_action_label: "Підв'язати",
          primary_action: async (values) => {
            const selected = accounts.find((a) => a.label === values.mono_account);
            if (!selected) {
              frappe.msgprint("Не вдалося визначити обраний рахунок");
              return;
            }
            await frappe.call({
              method: "ukrainian_integrations.payments.monobank.service.mono_bind_account",
              args: { account_id: selected.id, bank_account: values.bank_account || null },
            });
            frm.reload_doc();
            frappe.show_alert({ message: "Рахунок Monobank підв'язано", indicator: "green" });
            d.hide();
          },
        });
        d.show();
      } catch (e) {
        frappe.msgprint({ title: "Помилка", indicator: "red", message: (e && e.message) || "Не вдалося завантажити рахунки" });
      }
    });

    frm.add_custom_button("📥 Тест імпорту (1 день)", async () => {
      try {
        const r = await frappe.call({
          method: "ukrainian_integrations.payments.monobank.service.mono_statements_import_to_bank_transactions",
          args: { days_back: 1 },
        });
        const m = r.message || {};
        frappe.msgprint(`Імпорт завершено. Створено: ${m.created || 0}, пропущено: ${m.skipped || 0}`);
      } catch (e) {
        frappe.msgprint({ title: "Помилка імпорту", indicator: "red", message: (e && e.message) || "Помилка" });
      }
    });
  },
});
