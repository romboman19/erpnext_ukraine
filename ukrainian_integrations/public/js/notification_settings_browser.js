function uaBrowserNotificationStatus() {
  if (!("Notification" in window)) return "unsupported";
  return Notification.permission;
}

frappe.ui.form.on("Notification Settings", {
  refresh(frm) {
    const status = uaBrowserNotificationStatus();
    if (status === "unsupported") {
      frm.add_custom_button(__("Enable Browser Notifications"), () => {
        frappe.msgprint(__("Browser notifications are not supported by this browser."));
      });
      return;
    }

    if (status === "granted") {
      frm.add_custom_button(__("Browser Notifications Enabled"), () => {
        frappe.show_alert({
          message: __("Browser notifications are enabled"),
          indicator: "green",
        });
      });
      return;
    }

    frm.add_custom_button(__("Enable Browser Notifications"), async () => {
      if (status === "denied") {
        frappe.msgprint(
          __("Browser notifications are blocked. Allow them in the browser site settings.")
        );
        return;
      }

      const permission = await Notification.requestPermission();
      if (permission === "granted") {
        frappe.show_alert({
          message: __("Browser notifications are enabled"),
          indicator: "green",
        });
        frm.reload_doc();
      }
    });
  },
});
