frappe.ui.form.on("Monobank Settings", {
  refresh(frm) {
    frm.add_custom_button("🔄 Завантажити рахунки Monobank", async () => {
      try {
        const profiles = (frm.doc.profiles || []).filter((p) => (p.enabled || 0) == 1);
        const profileOptions = profiles.map((p) => `${p.name} | ${p.label || "(без назви)"}`);

        const pickProfile = new frappe.ui.Dialog({
          title: "Оберіть профіль Monobank",
          fields: [
            { fieldname: "profile", label: "Profile", fieldtype: "Select", options: profileOptions.join("\n"), reqd: profileOptions.length > 0 },
          ],
          primary_action_label: "Далі",
          primary_action: async (vals) => {
            pickProfile.hide();
            const profileName = (vals.profile || "").split(" | ")[0] || null;

            const r = await frappe.call({
              method: "ukrainian_integrations.payments.monobank.service.mono_list_accounts",
              args: { profile: profileName },
            });
            const accounts = (r.message && r.message.accounts) || [];
            if (!accounts.length) {
              frappe.msgprint("Не знайдено рахунків Monobank");
              return;
            }

            const d = new frappe.ui.Dialog({
              title: "Вибір рахунку Monobank",
              fields: [
                { fieldname: "mono_account", label: "Monobank Account", fieldtype: "Select", options: accounts.map((a) => a.label).join("\n"), reqd: 1 },
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
                  args: { account_id: selected.id, bank_account: values.bank_account || null, profile: profileName },
                });
                await frm.reload_doc();
                frappe.show_alert({ message: "Рахунок Monobank підв'язано", indicator: "green" });
                d.hide();
              },
            });
            d.show();
          },
        });

        if (profileOptions.length) {
          pickProfile.show();
        } else {
          // fallback for legacy single settings
          pickProfile.set_value("profile", "");
          pickProfile.primary_action({ profile: "" });
        }
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
        const r = await frappe.call({
          method: "ukrainian_integrations.payments.monobank.service.mono_statements_import_to_bank_transactions",
          args: { days_back: 1, profile: profileName },
        });
        const m = r.message || {};
        frappe.msgprint(`Імпорт завершено. Створено: ${m.created || 0}, пропущено: ${m.skipped || 0}`);
      } catch (e) {
        frappe.msgprint({ title: "Помилка імпорту", indicator: "red", message: (e && e.message) || "Помилка" });
      }
    });
  },
});
