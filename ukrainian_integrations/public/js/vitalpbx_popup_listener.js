(function () {
  if (window.__vitalpbx_popup_bound) return;
  window.__vitalpbx_popup_bound = true;
  var seen = {};

  function esc(v) { return frappe.utils.escape_html((v || '').toString()); }

  frappe.realtime.on('vitalpbx_call_popup', async function (m) {
    if (!m || !m.call_id) return;
    var key = m.call_id + ':' + (m.status || '');
    if (seen[key]) return;
    seen[key] = true;

    try {
      const context = await frappe.call({
        method: 'ukrainian_integrations.pbx_sms.vitalpbx.events.get_call_context',
        args: { call_id: m.call_id }
      });
      Object.assign(m, (context && context.message) || {});
    } catch (_) {
      m.customer = null;
      m.recent_sales_invoices = [];
    }

    var c = m.customer || null;
    var side = (m.direction || 'inbound') === 'inbound' ? ('Вхідний: ' + (m.from_number || '')) : ('Вихідний: ' + (m.to_number || ''));

    var html = '<div><b>VitalPBX</b> · ' + esc((m.status || '').toUpperCase()) + '</div>';
    html += '<div style="margin-top:6px">' + esc(side) + '</div>';

    if (c) {
      html += '<hr><div><b>Клієнт:</b> ' + esc(c.customer_name || c.name) + '</div>';
      html += '<div>Тел: ' + esc(c.mobile_no || c.phone || '') + '</div>';
      html += '<div style="margin-top:6px"><a href="/app/customer/' + encodeURIComponent(c.name) + '" target="_blank">Відкрити клієнта</a></div>';
    }

    if (Array.isArray(m.recent_sales_invoices) && m.recent_sales_invoices.length) {
      html += '<hr><div><b>Останні замовлення:</b></div><ul style="margin-top:4px">';
      m.recent_sales_invoices.forEach(function (si) {
        html += '<li><a href="/app/sales-invoice/' + encodeURIComponent(si.name) + '" target="_blank">' + esc(si.name) + '</a> · ' + esc(si.posting_date) + ' · ' + esc(si.total) + ' · ' + esc(si.status || '') + '</li>';
      });
      html += '</ul>';
    }

    frappe.msgprint({ title: 'Дзвінок VitalPBX', message: html, indicator: ((m.status || '').indexOf('fail') >= 0 || (m.status || '').indexOf('miss') >= 0) ? 'red' : 'blue', wide: true });
  });
})();
