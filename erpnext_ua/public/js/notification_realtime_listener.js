function uaNotificationText(value) {
  const element = document.createElement("div");
  element.innerHTML = String(value || "");
  return (element.textContent || element.innerText || "").trim();
}

function uaOpenNotification(notificationLog) {
  if (!notificationLog.document_type || !notificationLog.document_name) return;
  frappe.set_route("Form", notificationLog.document_type, notificationLog.document_name);
}

function uaShowBrowserNotification(notificationLog, message) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!document.hidden) return;

  const browserNotification = new Notification(__("New ERPNext notification"), {
    body: message,
    icon: "/assets/erpnext_ua/images/app-logo.svg",
    tag: `erpnext-${notificationLog.name}`,
  });
  browserNotification.onclick = () => {
    window.focus();
    uaOpenNotification(notificationLog);
    browserNotification.close();
  };
}

function uaShowRealtimeNotification(notificationLog) {
  if (!notificationLog) return;
  if (window.uaLastNotificationName === notificationLog.name) return;
  window.uaLastNotificationName = notificationLog.name;

  const message = uaNotificationText(notificationLog.subject);
  if (!message) return;

  frappe.show_alert(
    {
      message: frappe.utils.escape_html(message),
      indicator: notificationLog.type === "Alert" ? "orange" : "blue",
    },
    10
  );
  if (frappe.utils.play_sound) frappe.utils.play_sound("alert");
  uaShowBrowserNotification(notificationLog, message);
}

function uaLoadLatestNotification() {
  return frappe
    .call({
      method: "frappe.desk.doctype.notification_log.notification_log.get_notification_logs",
      type: "GET",
      args: { limit: 1 },
    })
    .then((response) => {
      const logs = response.message && response.message.notification_logs;
      uaShowRealtimeNotification(logs && logs[0]);
    });
}

if (!window.uaNotificationListenerRegistered) {
  window.uaNotificationListenerRegistered = true;
  frappe.realtime.on("notification", uaLoadLatestNotification);
}
