frappe.ui.form.on("Telegram Bot Profile", {
  refresh(frm) {
    if (
      frm.is_new() ||
      !frm.doc.enabled ||
      !frappe.user.has_role(["Sales Manager", "System Manager"])
    )
      return;

    frm.add_custom_button(__("Send Test Message"), () => {
      const idempotencyKey =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : frappe.utils.get_random(32);
      const dialog = new frappe.ui.Dialog({
        title: __("Telegram Test Message"),
        fields: [
          {
            fieldname: "chat_id",
            fieldtype: "Data",
            label: __("Chat ID"),
            reqd: 1,
          },
          {
            fieldname: "text",
            fieldtype: "Small Text",
            label: __("Message"),
            default: __("Test message from ERPNext"),
            reqd: 1,
          },
        ],
        primary_action_label: __("Queue"),
        async primary_action(values) {
          await frappe.call({
            method:
              "ukrainian_integrations.communication.telegram.service.send_test_message",
            type: "POST",
            args: {
              bot_profile: frm.doc.name,
              chat_id: values.chat_id,
              text: values.text,
              idempotency_key: idempotencyKey,
            },
          });
          dialog.hide();
          frappe.show_alert({ message: __("Telegram message queued"), indicator: "green" });
        },
      });
      dialog.show();
    });
  },
});
