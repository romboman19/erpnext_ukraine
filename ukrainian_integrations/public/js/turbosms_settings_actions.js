frappe.ui.form.on("TurboSMS Settings", {
  refresh(frm) {
    frm.add_custom_button("📨 Надіслати SMS", async () => {
      let senderOptions = [];
      let defaultSender = "";

      try {
        const r = await frappe.call({
          method: "ukrainian_integrations.pbx_sms.sms.turbosms.get_sender_options",
        });
        senderOptions = (r.message && r.message.senders) || [];
        defaultSender = (r.message && r.message.default_sender) || "";
      } catch (_) {}

      const idempotencyKey =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : frappe.utils.get_random(32);

      const d = new frappe.ui.Dialog({
        title: "TurboSMS: Відправка повідомлення",
        fields: [
          {
            fieldname: "sender",
            label: "Sender",
            fieldtype: "Select",
            options: senderOptions.join("\n"),
            default: defaultSender,
            reqd: 1,
          },
          { fieldname: "phone", label: "Телефон", fieldtype: "Data", reqd: 1 },
          { fieldname: "text", label: "Текст", fieldtype: "Small Text", reqd: 1 },
        ],
        primary_action_label: "Відправити",
        primary_action: async (values) => {
          try {
            const r = await frappe.call({
              method: "ukrainian_integrations.pbx_sms.sms.turbosms.send_sms_from_settings",
              args: {
                sender: values.sender,
                phone: values.phone,
                text: values.text,
                idempotency_key: idempotencyKey,
              },
            });

            const m = (r && r.message) || {};
            frappe.show_alert({
              message: "SMS надіслано: " + (m.phone || values.phone),
              indicator: "green",
            });
            d.hide();
          } catch (e) {
            frappe.msgprint({
              title: "Помилка",
              indicator: "red",
              message: (e && e.message) || "Не вдалося надіслати SMS",
            });
          }
        },
      });

      d.show();
    });
  },
});
