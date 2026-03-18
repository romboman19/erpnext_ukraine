frappe.ui.form.on("PrivatBank Settings", {
  refresh(frm) {
    frm.add_custom_button("🔄 Завантажити рахунки PrivatBank", async () => {
      try {
        const profiles = (frm.doc.profiles || []).filter((p) => (p.enabled || 0) == 1);
        if (!profiles.length) {
          frappe.msgprint("Немає активних профілів");
          return;
        }
        const profileOptions = profiles.map((p) => `${p.name} | ${p.label || "(без назви)"}`);
        const d0 = new frappe.ui.Dialog({
          title: "Оберіть профіль PrivatBank",
          fields: [{ fieldname: "profile", label: "Profile", fieldtype: "Select", options: profileOptions.join("\n"), reqd: 1 }],
          primary_action_label: "Далі",
          primary_action: async (v0) => {
            d0.hide();
            const profileName = (v0.profile || "").split(" | ")[0];
            const r = await frappe.call({
              method: "ukrainian_integrations.payments.privatbank.service.pb_list_accounts",
              args: { profile: profileName },
            });
            const accounts = (r.message && r.message.accounts) || [];
            if (!accounts.length) {
              frappe.msgprint("Не знайдено рахунків PrivatBank (перевір токен/API)");
              return;
            }
            const d = new frappe.ui.Dialog({
              title: "Вибір рахунку PrivatBank",
              fields: [
                { fieldname: "account", label: "Privat Account", fieldtype: "Select", options: accounts.map((a) => a.label).join("\n"), reqd: 1 },
                { fieldname: "bank_account", label: "ERP Bank Account", fieldtype: "Link", options: "Bank Account" },
              ],
              primary_action_label: "Підв'язати",
              primary_action: async (vals) => {
                const selected = accounts.find((a) => a.label === vals.account);
                await frappe.call({
                  method: "ukrainian_integrations.payments.privatbank.service.pb_bind_account",
                  args: { profile: profileName, account: (selected && selected.account) || vals.account, bank_account: vals.bank_account || null },
                });
                await frm.reload_doc();
                frappe.show_alert({ message: "Рахунок PrivatBank підв'язано", indicator: "green" });
                d.hide();
              },
            });
            d.show();
          },
        });
        d0.show();
      } catch (e) {
        frappe.msgprint({ title: "Помилка", indicator: "red", message: (e && e.message) || "Не вдалося завантажити рахунки" });
      }
    });

    frm.add_custom_button("📥 Тест імпорту (1 день)", async () => {
      try {
        const profiles = (frm.doc.profiles || []).filter((p) => (p.enabled || 0) == 1);
        let profileName = null;
        if (profiles.length) {
          const def = profiles.find((p) => (p.is_default || 0) == 1) || profiles[0];
          profileName = def.name;
        }
        const today = frappe.datetime.get_today();
        const r = await frappe.call({
          method: "ukrainian_integrations.payments.privatbank.service.pb_statements_import_to_bank_transactions",
          args: { start_date: today, end_date: today, profile: profileName },
        });
        const m = r.message || {};
        frappe.msgprint(`Імпорт завершено. Створено: ${m.created || 0}, пропущено: ${m.skipped || 0}`);
      } catch (e) {
        frappe.msgprint({ title: "Помилка імпорту", indicator: "red", message: (e && e.message) || "Помилка" });
      }
    });
  },
});
