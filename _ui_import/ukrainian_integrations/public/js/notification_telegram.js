const UA_TELEGRAM_PARTY_DOCTYPES = ["User", "Customer", "Employee", "Supplier"];

function uaTelegramReceiverFields(frm) {
  if (!frm.doc.document_type) return [];

  const documentMeta = frappe.get_doc("DocType", frm.doc.document_type);
  if (!documentMeta) return [];
  const fields = documentMeta.fields || [];
  const optionFor = (field, parentField) => ({
    value: parentField ? `${field.fieldname},${parentField}` : field.fieldname,
    label: parentField
      ? `${parentField} > ${field.fieldname} (${__(field.label, null, field.parent)})`
      : `${field.fieldname} (${__(field.label, null, field.parent)})`,
  });
  const isPartyLink = (field) =>
    field.fieldtype === "Link" && UA_TELEGRAM_PARTY_DOCTYPES.includes(field.options);

  return fields.flatMap((field) => {
    if (frappe.model.table_fields.includes(field.fieldtype)) {
      const child = frappe.get_doc("DocType", field.options);
      return (child && child.fields ? child.fields : [])
        .filter(isPartyLink)
        .map((childField) => optionFor(childField, field.fieldname));
    }
    return isPartyLink(field) ? [optionFor(field)] : [];
  });
}

function uaSetupTelegramNotification(frm) {
  const isTelegram = frm.doc.channel === "Telegram";
  const subjectChannels = ["Email", "Slack", "System Notification", "Telegram"];

  frm.toggle_display("subject", subjectChannels.includes(frm.doc.channel));
  frm.set_df_property("subject", "reqd", subjectChannels.includes(frm.doc.channel));
  frm.toggle_display("send_to_all_assignees", ["Email", "Telegram"].includes(frm.doc.channel));
  frm.set_query("ua_telegram_bot_profile", () => ({ filters: { enabled: 1 } }));

  const grid = frm.fields_dict.recipients && frm.fields_dict.recipients.grid;
  if (grid) {
    grid.update_docfield_property("ua_telegram_chat_id", "hidden", !isTelegram);
    if (isTelegram) {
      grid.update_docfield_property(
        "receiver_by_document_field",
        "options",
        ["", "owner"].concat(uaTelegramReceiverFields(frm))
      );
    }
  }

  if (!isTelegram) return;
  frm.set_df_property(
    "channel",
    "description",
    `${__("Configure an enabled")} <a href="/app/telegram-bot-profile">${__(
      "Telegram Bot Profile"
    )}</a>.`
  );
  const examples = frm.get_field("message_examples");
  if (examples) {
    examples.html(
      `<h5>${__("Telegram message example")}</h5><pre>${frappe.utils.escape_html(
        "Замовлення {{ doc.name }} готове до відправлення."
      )}</pre>`
    );
  }
}

frappe.ui.form.on("Notification", {
  refresh: uaSetupTelegramNotification,
  channel: uaSetupTelegramNotification,
  document_type(frm) {
    if (!frm.doc.document_type) return;
    frappe.model.with_doctype(frm.doc.document_type, () => uaSetupTelegramNotification(frm));
  },
});
