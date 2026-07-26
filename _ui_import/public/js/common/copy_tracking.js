frappe.provide(ukrainian_integrations.ui);
ukrainian_integrations.ui.copyTracking = function(value) {
  if (!value) return;
  navigator.clipboard?.writeText(value);
};
