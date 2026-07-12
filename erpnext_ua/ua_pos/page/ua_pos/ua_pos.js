frappe.pages["ua-pos"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: "Каса", single_column: true });
  const state = {
    token: sessionStorage.getItem("ua_pos_token"),
    session: null,
    order: null,
    saleMode: "Non Fiscal",
    clock: null,
  };
  wrapper.uaPosState = state;

  const statusLabels = {
    Building: "Формування чека",
    Held: "Відкладено",
    "Awaiting Payment": "Очікує оплату",
    "Payment In Progress": "Оплата виконується",
    "Payment Unknown": "Статус оплати невідомий",
    Paid: "Оплачено",
    Posting: "Проведення",
    Posted: "Проведено",
    "Fiscal Pending": "Очікує фіскалізацію",
    Completed: "Завершено",
    "Completed Print Error": "Помилка друку",
    "Manual Review": "Потрібна перевірка",
    Cancelled: "Скасовано",
  };
  const esc = (value) => frappe.utils.escape_html(String(value ?? ""));
  const money = (value) => format_number(flt(value || 0), null, 2);
  const idem = () => crypto.randomUUID();
  const api = (method, args = {}) =>
    frappe.call({ method: `erpnext_ua.ua_pos.api.${method}`, args }).then((response) => response.message);
  const identificationApi = (method, args = {}) =>
    frappe.call({ method: `ukrainian_integrations.customer_identification.service.${method}`, args }).then((response) => response.message);

  const styles = `<style id="ua-pos-v2-styles">
    .layout-main-section-wrapper{margin-bottom:0!important}.layout-main-section{padding:0!important}.page-head{display:none!important}
    .ua-pos{--ink:#172033;--muted:#667085;--line:#dfe4ec;--panel:#fff;--bg:#f3f6fa;--blue:#2563eb;--blue2:#1d4ed8;--green:#079455;--amber:#dc6803;--red:#d92d20;min-height:calc(100vh - 48px);background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
    .ua-pos *{box-sizing:border-box}.ua-pos button,.ua-pos input{font:inherit}.ua-pos-login-screen{min-height:calc(100vh - 48px);display:grid;place-items:center;padding:32px;background:radial-gradient(circle at 15% 10%,#dbeafe 0,transparent 34%),radial-gradient(circle at 85% 85%,#d1fae5 0,transparent 30%),#f8fafc}
    .ua-pos-login-card{width:min(520px,100%);background:#fff;border:1px solid #e4e7ec;border-radius:20px;box-shadow:0 24px 70px rgba(16,24,40,.14);padding:36px}.ua-pos-brand{display:flex;align-items:center;gap:12px}.ua-pos-logo{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#2563eb,#0f766e);display:grid;place-items:center;color:#fff;font-weight:800;font-size:20px}.ua-pos-brand strong{font-size:20px}.ua-pos-brand small{display:block;color:var(--muted);margin-top:2px}.ua-pos-login-card h1{font-size:27px;margin:32px 0 6px}.ua-pos-login-card>p{color:var(--muted);margin:0 0 24px}.ua-pos-field{margin:14px 0}.ua-pos-field label{display:block;font-weight:650;font-size:13px;margin-bottom:7px}.ua-pos-field input{width:100%;height:48px;border:1px solid #cfd6e2;border-radius:10px;padding:0 14px;outline:0}.ua-pos-field input:focus{border-color:var(--blue);box-shadow:0 0 0 3px #dbeafe}.ua-pos-login-button{width:100%;height:50px;border:0;border-radius:10px;background:var(--blue);color:#fff;font-weight:750;margin-top:10px}.ua-pos-login-note{margin-top:18px;padding:11px 13px;background:#f8fafc;border-radius:9px;color:var(--muted);font-size:12px}
    .ua-pos-workspace{display:none;min-height:calc(100vh - 48px)}.ua-pos-topbar{height:66px;background:#111827;color:#fff;display:flex;align-items:center;gap:18px;padding:0 20px;position:sticky;top:0;z-index:20}.ua-pos-topbar .ua-pos-brand{min-width:210px}.ua-pos-topbar .ua-pos-logo{width:38px;height:38px;font-size:17px}.ua-pos-topbar .ua-pos-brand strong{font-size:16px}.ua-pos-topbar .ua-pos-brand small{color:#98a2b3}.ua-pos-statuses{display:flex;gap:8px;align-items:center;overflow:hidden;flex:1}.ua-pos-chip{height:34px;display:flex;align-items:center;gap:7px;padding:0 11px;border:1px solid #344054;border-radius:8px;color:#d0d5dd;font-size:12px;white-space:nowrap}.ua-pos-chip b{color:#fff;font-weight:650}.ua-pos-dot{width:8px;height:8px;border-radius:50%;background:#98a2b3}.ua-pos-dot.ok{background:#32d583}.ua-pos-dot.warn{background:#fdb022}.ua-pos-user{display:flex;align-items:center;gap:10px;border-left:1px solid #344054;padding-left:17px;white-space:nowrap}.ua-pos-avatar{width:34px;height:34px;border-radius:50%;background:#344054;display:grid;place-items:center;font-weight:700}.ua-pos-user strong{display:block;font-size:12px}.ua-pos-user span{display:block;font-size:11px;color:#98a2b3}.ua-pos-icon-button{border:0;background:transparent;color:#d0d5dd;font-size:18px;padding:8px}
    .ua-pos-command{padding:14px 18px 10px;background:#fff;border-bottom:1px solid var(--line)}.ua-pos-command-top{display:flex;gap:10px;align-items:stretch}.ua-pos-search-wrap{position:relative;flex:1}.ua-pos-search-icon{position:absolute;left:15px;top:13px;font-size:20px;color:var(--blue)}.ua-pos-scan{height:50px;width:100%;border:2px solid #b8c5d8;border-radius:10px;padding:0 135px 0 46px;font-size:17px;font-weight:600;outline:0}.ua-pos-scan:focus{border-color:var(--blue);box-shadow:0 0 0 3px #dbeafe}.ua-pos-keyhint{position:absolute;right:12px;top:12px;border:1px solid #d0d5dd;background:#f9fafb;border-radius:6px;padding:4px 8px;font-size:11px;color:var(--muted)}.ua-pos-mode{display:flex;border:1px solid #d0d5dd;border-radius:10px;padding:4px;background:#f8fafc}.ua-pos-mode button{border:0;background:transparent;border-radius:7px;padding:0 14px;font-size:12px;font-weight:700;color:var(--muted)}.ua-pos-mode button.active{background:#fff;color:var(--blue);box-shadow:0 1px 4px rgba(16,24,40,.12)}.ua-pos-mode button.fiscal.active{color:#b54708;background:#fffaeb}.ua-pos-command-actions{display:flex;gap:7px;margin-top:10px;overflow-x:auto;padding-bottom:1px}.ua-pos-action{height:38px;border:1px solid #d0d5dd;border-radius:8px;background:#fff;padding:0 12px;display:flex;align-items:center;gap:7px;color:#344054;font-size:12px;font-weight:650;white-space:nowrap}.ua-pos-action:hover{background:#f9fafb;border-color:#98a2b3}.ua-pos-action.primary{background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8}.ua-pos-action.danger{color:#b42318}.ua-pos-action[disabled]{opacity:.45;cursor:not-allowed}.ua-pos-shortcut{font-size:10px;color:#98a2b3;border-left:1px solid #d0d5dd;padding-left:7px}
    .ua-pos-alert{display:none;margin:12px 18px 0;padding:10px 13px;border-radius:8px;background:#fffaeb;border:1px solid #fedf89;color:#93370d;font-size:13px}.ua-pos-main{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:14px;padding:14px 18px 18px;height:calc(100vh - 192px)}.ua-pos-panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 2px rgba(16,24,40,.04);overflow:hidden}.ua-pos-cart-panel{display:flex;flex-direction:column;min-width:0}.ua-pos-sale-info{min-height:62px;display:flex;align-items:center;gap:22px;padding:9px 14px;border-bottom:1px solid var(--line);background:#fbfcfe}.ua-pos-sale-info-item{min-width:0}.ua-pos-sale-info-item label{display:block;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}.ua-pos-sale-info-item strong{display:block;font-size:13px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ua-pos-customer-button{margin-left:auto;border:1px solid #d0d5dd;border-radius:8px;background:#fff;height:36px;padding:0 12px;color:#344054;font-weight:650;font-size:12px}
    .ua-pos-table-wrap{flex:1;overflow:auto;position:relative}.ua-pos-table{border-collapse:separate;border-spacing:0;width:100%;min-width:1100px;font-size:12px}.ua-pos-table th{position:sticky;top:0;z-index:2;background:#f2f4f7;color:#475467;text-align:left;padding:9px 10px;border-bottom:1px solid #d0d5dd;font-weight:700;white-space:nowrap}.ua-pos-table td{padding:9px 10px;border-bottom:1px solid #eaecf0;vertical-align:middle}.ua-pos-table tbody tr:hover{background:#f8fbff}.ua-pos-table .num{text-align:right;font-variant-numeric:tabular-nums}.ua-pos-item-name{font-weight:700;color:#1d2939;max-width:320px}.ua-pos-item-code{font-size:10px;color:var(--muted);margin-top:2px}.ua-pos-qty{display:inline-flex;align-items:center;border:1px solid #d0d5dd;border-radius:7px;overflow:hidden}.ua-pos-qty button{width:26px;height:26px;border:0;background:#f9fafb;color:#344054}.ua-pos-qty span{min-width:38px;text-align:center;font-weight:700}.ua-pos-empty{position:absolute;inset:44px 0 0;display:grid;place-items:center;text-align:center;color:var(--muted)}.ua-pos-empty-icon{font-size:42px;opacity:.35}.ua-pos-empty strong{display:block;color:#475467;font-size:16px;margin:8px}.ua-pos-cart-footer{height:42px;border-top:1px solid var(--line);display:flex;align-items:center;gap:20px;padding:0 14px;background:#fbfcfe;color:var(--muted);font-size:11px}.ua-pos-cart-footer b{color:#344054}
    .ua-pos-summary{display:flex;flex-direction:column}.ua-pos-summary-head{padding:15px 16px;border-bottom:1px solid var(--line)}.ua-pos-summary-head span{font-size:11px;color:var(--muted)}.ua-pos-summary-head strong{display:block;font-size:15px;margin-top:3px}.ua-pos-order-badge{display:inline-flex!important;width:auto;margin-top:8px!important;padding:3px 7px;border-radius:5px;background:#f2f4f7;color:#475467!important;font-size:10px!important}.ua-pos-totals{padding:14px 16px}.ua-pos-total-row{display:flex;justify-content:space-between;align-items:center;margin:9px 0;color:#475467;font-size:13px}.ua-pos-total-row strong{color:#1d2939;font-variant-numeric:tabular-nums}.ua-pos-total-row.discount strong{color:var(--green)}.ua-pos-due{margin-top:auto;padding:18px 16px 16px;border-top:1px solid var(--line);background:#f8fafc}.ua-pos-due-label{font-size:12px;color:var(--muted);font-weight:650}.ua-pos-due-value{font-size:38px;line-height:1.1;font-weight:850;letter-spacing:-.04em;margin:5px 0 15px;color:#101828;font-variant-numeric:tabular-nums}.ua-pos-pay-main{height:54px;width:100%;border:0;border-radius:10px;background:var(--green);color:#fff;font-size:16px;font-weight:800}.ua-pos-pay-main:disabled{background:#98a2b3}.ua-pos-pay-split{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}.ua-pos-pay-split button{height:42px;border:1px solid #d0d5dd;border-radius:8px;background:#fff;color:#344054;font-weight:700;font-size:12px}.ua-pos-pay-split button.card{color:#1d4ed8;border-color:#bfdbfe;background:#eff6ff}.ua-pos-footer-status{display:flex;gap:13px;padding:8px 16px;border-top:1px solid var(--line);font-size:10px;color:var(--muted)}
    .ua-pos-denoms{width:100%;border-collapse:collapse}.ua-pos-denoms th,.ua-pos-denoms td{border-bottom:1px solid #eaecf0;padding:7px 9px;text-align:right}.ua-pos-denoms th:first-child,.ua-pos-denoms td:first-child{text-align:left}.ua-pos-denoms input{width:92px;height:32px;border:1px solid #d0d5dd;border-radius:6px;padding:0 8px;text-align:right}.ua-pos-denom-total{font-size:20px;font-weight:800;text-align:right;padding:12px 0}.ua-pos-modal-note{padding:9px 11px;background:#f2f4f7;border-radius:7px;color:#475467;font-size:12px;margin-bottom:10px}
    @media(max-width:1050px){.ua-pos-main{grid-template-columns:minmax(0,1fr) 290px}.ua-pos-statuses .ua-pos-chip:nth-child(n+4){display:none}}@media(max-width:800px){.ua-pos-main{grid-template-columns:1fr;height:auto}.ua-pos-summary{min-height:360px}.ua-pos-topbar{padding:0 12px}.ua-pos-topbar .ua-pos-brand{min-width:auto}.ua-pos-topbar .ua-pos-brand>div:last-child,.ua-pos-statuses{display:none}.ua-pos-command-top{flex-wrap:wrap}.ua-pos-mode{height:48px}.ua-pos-main{padding:10px}.ua-pos-command{padding:10px}}
  </style>`;
  $("#ua-pos-v2-styles").remove();
  $(styles).appendTo(document.head);

  const $root = $(`
    <div class="ua-pos">
      <section class="ua-pos-login-screen">
        <div class="ua-pos-login-card">
          <div class="ua-pos-brand"><div class="ua-pos-logo">UA</div><div><strong>ERPNext Ukraine</strong><small>Каса · POS</small></div></div>
          <h1>Робоче місце касира</h1>
          <p>Оберіть касу та відскануйте персональний штрихкод працівника.</p>
          <div class="ua-pos-field"><label>Каса</label><input class="ua-pos-login-desk" value="POS Test Desk" autocomplete="off"></div>
          <div class="ua-pos-field"><label>Штрихкод працівника</label><input class="ua-pos-login-barcode" type="password" autocomplete="off" placeholder="Відскануйте або введіть код"></div>
          <button class="ua-pos-login-button">Увійти до каси</button>
          <div class="ua-pos-login-note">Тестовий касир: <b>POS-TEST-CASHIER</b>. У продуктивному режимі використовується персональна картка працівника.</div>
        </div>
      </section>
      <section class="ua-pos-workspace">
        <header class="ua-pos-topbar">
          <div class="ua-pos-brand"><div class="ua-pos-logo">UA</div><div><strong>Каса</strong><small class="js-desk">—</small></div></div>
          <div class="ua-pos-statuses">
            <div class="ua-pos-chip"><i class="ua-pos-dot js-shift-dot"></i><span>Зміна</span><b class="js-shift">закрита</b></div>
            <div class="ua-pos-chip"><i class="ua-pos-dot ok"></i><span>Склад</span><b class="js-warehouse">—</b></div>
            <div class="ua-pos-chip"><i class="ua-pos-dot js-prro-dot"></i><span>ПРРО</span><b class="js-prro">не налаштовано</b></div>
            <div class="ua-pos-chip"><i class="ua-pos-dot js-terminal-dot"></i><span>Термінал</span><b class="js-terminal">не налаштовано</b></div>
          </div>
          <div class="ua-pos-user"><div class="ua-pos-avatar js-avatar">К</div><div><strong class="js-employee">—</strong><span class="js-clock"></span></div><button class="ua-pos-icon-button js-logout" title="Вийти">↪</button></div>
        </header>
        <section class="ua-pos-command">
          <div class="ua-pos-command-top">
            <div class="ua-pos-search-wrap"><span class="ua-pos-search-icon">⌕</span><input class="ua-pos-scan" autocomplete="off" placeholder="Штрихкод, артикул або назва товару"><span class="ua-pos-keyhint">F2 · Enter</span></div>
            <div class="ua-pos-mode"><button data-mode="Non Fiscal" class="active">Без фіскалізації</button><button data-mode="Fiscal" class="fiscal">Фіскальний продаж</button></div>
          </div>
          <div class="ua-pos-command-actions">
            <button class="ua-pos-action primary js-new-order">＋ Новий чек</button>
            <button class="ua-pos-action js-stock">⌕ Пошук по складу <span class="ua-pos-shortcut">F3</span></button>
            <button class="ua-pos-action js-customer">♙ Клієнт <span class="ua-pos-shortcut">F4</span></button>
            <button class="ua-pos-action primary js-identify">◎ Ідентифікувати <span class="ua-pos-shortcut">F5</span></button>
            <button class="ua-pos-action js-hold">◫ Відкласти <span class="ua-pos-shortcut">F7</span></button>
            <button class="ua-pos-action js-return">↩ Повернення <span class="ua-pos-shortcut">F8</span></button>
            <button class="ua-pos-action js-cash-menu">₴ Операції з касою</button>
            <button class="ua-pos-action js-fiscal-menu">▣ Фіскальне меню</button>
            <button class="ua-pos-action js-reports">▤ Звіти</button>
            <button class="ua-pos-action danger js-cancel">× Скасувати чек</button>
          </div>
        </section>
        <div class="ua-pos-alert js-alert"></div>
        <main class="ua-pos-main">
          <section class="ua-pos-panel ua-pos-cart-panel">
            <div class="ua-pos-sale-info">
              <div class="ua-pos-sale-info-item"><label>Клієнт</label><strong class="js-customer-name">Роздрібний покупець</strong></div>
              <div class="ua-pos-sale-info-item"><label>ФОП</label><strong class="js-fop">Визначається правилами</strong></div>
              <div class="ua-pos-sale-info-item"><label>Статус чека</label><strong class="js-order-status">Новий чек</strong></div>
              <button class="ua-pos-customer-button js-identify">Ідентифікувати покупця</button>
            </div>
            <div class="ua-pos-table-wrap">
              <table class="ua-pos-table"><thead><tr><th>Товар / артикул</th><th>Штрихкод</th><th class="num">Кількість</th><th>Од.</th><th class="num">Ціна</th><th class="num">Знижка</th><th class="num">Сума</th><th>Партія / серійний №</th><th>Перевірка</th></tr></thead><tbody class="js-cart-body"></tbody></table>
              <div class="ua-pos-empty js-empty"><div><div class="ua-pos-empty-icon">▦</div><strong>Чек порожній</strong><span>Відскануйте штрихкод або введіть артикул у полі зверху</span></div></div>
            </div>
            <div class="ua-pos-cart-footer"><span>Артикулів: <b class="js-lines">0</b></span><span>Кількість: <b class="js-qty">0</b></span><span>Гарячі клавіші: <b>F2 пошук · F4 клієнт · F8 повернення · F9 оплатити</b></span></div>
          </section>
          <aside class="ua-pos-panel ua-pos-summary">
            <div class="ua-pos-summary-head"><span>Поточний документ</span><strong class="js-order-name">Чек ще не створено</strong><span class="ua-pos-order-badge js-order-badge">ГОТОВО ДО РОБОТИ</span></div>
            <div class="ua-pos-totals">
              <div class="ua-pos-total-row"><span>Повна сума</span><strong><span class="js-net">0,00</span> грн</strong></div>
              <div class="ua-pos-total-row discount"><span>Знижка</span><strong>− <span class="js-discount">0,00</span> грн</strong></div>
              <div class="ua-pos-total-row"><span>Бонуси</span><strong>0,00 грн</strong></div>
              <div class="ua-pos-total-row"><span>Отримано</span><strong><span class="js-paid">0,00</span> грн</strong></div>
              <div class="ua-pos-total-row"><span>Решта</span><strong><span class="js-change">0,00</span> грн</strong></div>
            </div>
            <div class="ua-pos-due"><div class="ua-pos-due-label">Сума до оплати</div><div class="ua-pos-due-value"><span class="js-total">0,00</span> <small>грн</small></div><button class="ua-pos-pay-main js-pay-cash" disabled>Оплатити · F9</button><div class="ua-pos-pay-split"><button class="js-pay-cash" disabled>Готівка</button><button class="card js-pay-card" disabled>Банківська картка</button></div></div>
            <div class="ua-pos-footer-status"><span>● ERP online</span><span class="js-footer-shift">○ зміна закрита</span><span class="js-footer-mode">○ без фіскалізації</span></div>
          </aside>
        </main>
      </section>
    </div>`).appendTo(page.body);

  function updateClock() {
    $root.find(".js-clock").text(new Intl.DateTimeFormat("uk-UA", { dateStyle: "short", timeStyle: "medium" }).format(new Date()));
  }

  function showNotice(message, kind = "warning") {
    const $alert = $root.find(".js-alert");
    $alert.text(message).css({ display: "block", background: kind === "error" ? "#fef3f2" : "#fffaeb", borderColor: kind === "error" ? "#fecdca" : "#fedf89", color: kind === "error" ? "#b42318" : "#93370d" });
  }

  function clearNotice() {
    $root.find(".js-alert").hide();
  }

  function canEditOrder() {
    return state.order && state.order.status === "Building";
  }

  function renderSession() {
    const session = state.session;
    if (!session) return;
    const desk = session.desk || {};
    $root.find(".js-desk").text(session.cash_desk);
    $root.find(".js-employee").text(session.employee_name || session.employee);
    $root.find(".js-avatar").text((session.employee_name || "К").trim().charAt(0).toUpperCase());
    $root.find(".js-warehouse").text(desk.warehouse || "не визначено");
    $root.find(".js-shift").text(session.shift ? session.shift : "закрита");
    $root.find(".js-shift-dot").toggleClass("ok", Boolean(session.shift));
    $root.find(".js-prro").text(desk.prro_cash_register || "не налаштовано");
    $root.find(".js-prro-dot").toggleClass("ok", Boolean(desk.prro_cash_register));
    $root.find(".js-terminal").text(desk.terminal || "не налаштовано");
    $root.find(".js-terminal-dot").toggleClass("ok", Boolean(desk.terminal));
    $root.find(".js-footer-shift").text(session.shift ? "● зміна відкрита" : "○ зміна закрита");
    if (!desk.prro_cash_register) state.saleMode = "Non Fiscal";
    $root.find(".ua-pos-mode button").removeClass("active").filter(`[data-mode="${state.saleMode}"]`).addClass("active");
    $root.find(".js-footer-mode").text(state.saleMode === "Fiscal" ? "● фіскальний режим" : "○ без фіскалізації");
    $root.find(".js-new-order").prop("disabled", !session.shift);
    if (!session.shift) showNotice("Управлінська зміна закрита. Відкрийте зміну через «Операції з касою», щоб почати продаж.");
    else clearNotice();
  }

  function renderOrder(order) {
    state.order = order || null;
    const items = order?.items || [];
    const editable = canEditOrder();
    $root.find(".js-empty").toggle(items.length === 0);
    $root.find(".js-order-name").text(order ? order.name : "Чек ще не створено");
    $root.find(".js-order-status").text(order ? statusLabels[order.status] || order.status : "Новий чек");
    $root.find(".js-order-badge").text(order ? (statusLabels[order.status] || order.status).toUpperCase() : "ГОТОВО ДО РОБОТИ");
    $root.find(".js-customer-name").text(order?.customer || "Роздрібний покупець");
    $root.find(".js-net").text(money(order?.net_total));
    $root.find(".js-discount").text(money(order?.discount_total));
    $root.find(".js-paid").text(money(order?.paid_total));
    $root.find(".js-change").text(money(order?.change_amount));
    $root.find(".js-total").text(money(order?.grand_total));
    $root.find(".js-lines").text(items.length);
    $root.find(".js-qty").text(money(items.reduce((sum, item) => sum + flt(item.qty), 0)));
    $root.find(".js-cart-body").html(items.map((item) => `
      <tr data-row="${esc(item.name)}"><td><div class="ua-pos-item-name">${esc(item.item_name || item.item_code)}</div><div class="ua-pos-item-code">${esc(item.item_code)}</div></td><td>${esc(item.barcode || "—")}</td><td class="num"><div class="ua-pos-qty"><button data-delta="-1" ${editable ? "" : "disabled"}>−</button><span>${esc(item.qty)}</span><button data-delta="1" ${editable ? "" : "disabled"}>＋</button></div></td><td>${esc(item.uom || "—")}</td><td class="num">${money(item.rate)}</td><td class="num">${money(item.discount_amount)}</td><td class="num"><b>${money(item.amount)}</b></td><td>${esc(item.batch_no || item.serial_no || "—")}</td><td><span style="color:#079455">● Готово</span></td></tr>`).join(""));
    const payable = Boolean(order && items.length && order.status === "Building" && state.session?.shift);
    $root.find(".js-pay-cash").prop("disabled", !payable);
    $root.find(".js-pay-card").prop("disabled", !payable || !state.session?.desk?.terminal);
    $root.find(".js-hold,.js-cancel,.js-customer,.js-identify").prop("disabled", !editable);
    $root.find(".js-hold").html(order?.status === "Held" ? "▶ Повернути чек <span class=\"ua-pos-shortcut\">F7</span>" : "◫ Відкласти <span class=\"ua-pos-shortcut\">F7</span>");
    if (order?.fiscal_mode) {
      state.saleMode = order.fiscal_mode;
      $root.find(".ua-pos-mode button").removeClass("active").filter(`[data-mode="${state.saleMode}"]`).addClass("active");
    }
    setTimeout(() => $root.find(".ua-pos-scan").focus(), 0);
  }

  async function refreshSession() {
    if (!state.token) return;
    try {
      state.session = await api("session_state", { pos_session_token: state.token });
      $root.find(".ua-pos-login-screen").hide();
      $root.find(".ua-pos-workspace").show();
      renderSession();
      const recoverable = (state.session.unfinished_orders || []).find((order) => ["Building", "Held"].includes(order.status));
      if (recoverable && !state.order) renderOrder(await api("get_order", { pos_session_token: state.token, order: recoverable.name }));
    } catch (error) {
      sessionStorage.removeItem("ua_pos_token");
      state.token = null;
      $root.find(".ua-pos-workspace").hide();
      $root.find(".ua-pos-login-screen").show();
    }
  }

  async function login() {
    const $barcode = $root.find(".ua-pos-login-barcode");
    try {
      const result = await api("login_by_barcode", { cash_desk: ($root.find(".ua-pos-login-desk").val() || "").trim(), barcode: ($barcode.val() || "").trim(), device_token: navigator.userAgent });
      state.token = result.session_token;
      sessionStorage.setItem("ua_pos_token", state.token);
      await refreshSession();
    } finally {
      $barcode.val("").focus();
    }
  }

  function denominationDialog({ title, expected = null, onSubmit }) {
    const denominations = [1000, 500, 200, 100, 50, 20, 10, 5, 2, 1, 0.5, 0.25, 0.1];
    const rows = denominations.map((value) => `<tr><td>${money(value)} грн</td><td><input type="number" min="0" step="1" value="0" data-denomination="${value}"></td><td class="js-row-total">0,00 грн</td></tr>`).join("");
    const dialog = new frappe.ui.Dialog({
      title,
      size: "large",
      fields: [
        { fieldname: "info", fieldtype: "HTML", options: `${expected === null ? "" : `<div class="ua-pos-modal-note">Очікуваний залишок: <b>${money(expected)} грн</b></div>`}<table class="ua-pos-denoms"><thead><tr><th>Номінал</th><th>Кількість</th><th>Сума</th></tr></thead><tbody>${rows}</tbody></table><div class="ua-pos-denom-total">Разом: <span>0,00</span> грн</div>` },
        ...(expected === null ? [] : [{ fieldname: "comment", fieldtype: "Small Text", label: "Коментар касира" }]),
      ],
      primary_action_label: "Підтвердити перерахунок",
      primary_action: async (values) => {
        const counted = [];
        dialog.$wrapper.find("[data-denomination]").each(function () { const qty = Math.max(0, parseInt(this.value || "0", 10)); if (qty) counted.push({ currency: "UAH", denomination: flt(this.dataset.denomination), qty }); });
        dialog.get_primary_btn().prop("disabled", true);
        try { await onSubmit(counted, values.comment || ""); dialog.hide(); } finally { dialog.get_primary_btn().prop("disabled", false); }
      },
    });
    dialog.show();
    const recalc = () => { let total = 0; dialog.$wrapper.find("[data-denomination]").each(function () { const rowTotal = flt(this.dataset.denomination) * Math.max(0, parseInt(this.value || "0", 10)); total += rowTotal; $(this).closest("tr").find(".js-row-total").text(`${money(rowTotal)} грн`); }); dialog.$wrapper.find(".ua-pos-denom-total span").text(money(total)); };
    dialog.$wrapper.on("input", "[data-denomination]", recalc);
    dialog.$wrapper.find("[data-denomination]").first().focus();
  }

  function openShift() {
    denominationDialog({ title: "Відкриття управлінської зміни", onSubmit: async (rows) => { await api("open_shift", { pos_session_token: state.token, denominations: JSON.stringify(rows), idem_key: idem() }); await refreshSession(); frappe.show_alert({ message: "Зміну відкрито", indicator: "green" }); } });
  }

  async function closeShift() {
    const summary = await api("close_shift_begin", { pos_session_token: state.token });
    if ((summary.blocking_orders || []).length) return showNotice(`Неможливо закрити зміну: незавершені чеки ${summary.blocking_orders.join(", ")}`, "error");
    denominationDialog({ title: "Закриття управлінської зміни", expected: summary.expected, onSubmit: async (rows, comment) => { await api("close_shift_confirm", { pos_session_token: state.token, denominations: JSON.stringify(rows), comment, idem_key: idem() }); renderOrder(null); await refreshSession(); frappe.show_alert({ message: "Зміну закрито", indicator: "green" }); } });
  }

  async function newOrder() {
    if (!state.session?.shift) return showNotice("Спочатку відкрийте управлінську зміну.", "error");
    renderOrder(await api("create_order", { pos_session_token: state.token, idem_key: idem(), fiscal_mode: state.saleMode }));
  }

  async function scan(query) {
    if (!state.session?.shift) return showNotice("Продаж неможливий: управлінська зміна закрита.", "error");
    if (!state.order || !canEditOrder()) await newOrder();
    renderOrder(await api("scan_item", { pos_session_token: state.token, order: state.order.name, query }));
  }

  function paymentDialog(kind) {
    if (!state.order?.items?.length) return;
    if (kind === "Card" && !state.session?.desk?.terminal) return showNotice("Для цієї каси не налаштовано банківський термінал.", "error");
    const total = flt(state.order.grand_total);
    const dialog = new frappe.ui.Dialog({
      title: kind === "Cash" ? "Оплата готівкою" : "Оплата банківською карткою",
      fields: [
        { fieldname: "due", fieldtype: "HTML", options: `<div class="ua-pos-modal-note">До сплати: <b style="font-size:20px">${money(total)} грн</b></div>` },
        { fieldname: "mode", fieldtype: "Link", options: "Mode of Payment", label: "Спосіб оплати", reqd: 1, default: kind === "Cash" ? "Cash" : "Credit Card" },
        ...(kind === "Cash" ? [{ fieldname: "received", fieldtype: "Currency", label: "Отримано від покупця", reqd: 1, default: total }, { fieldname: "change", fieldtype: "HTML", options: `<div class="ua-pos-denom-total" style="text-align:left">Решта: <span>0,00</span> грн</div>` }] : []),
      ],
      primary_action_label: kind === "Cash" ? "Підтвердити оплату" : "Надіслати на термінал",
      primary_action: async (values) => {
        if (kind === "Cash" && flt(values.received) < total) return frappe.msgprint("Отримана сума менша за суму до сплати.");
        dialog.get_primary_btn().prop("disabled", true);
        try {
          const completed = await api("checkout_start", { pos_session_token: state.token, order: state.order.name, payments: JSON.stringify([{ mode_of_payment: values.mode, kind, amount: total, currency: "UAH" }]), idem_key: idem() });
          renderOrder(completed); dialog.hide(); frappe.show_alert({ message: `${completed.name}: ${statusLabels[completed.status] || completed.status}`, indicator: completed.status === "Completed" ? "green" : "orange" });
        } finally { dialog.get_primary_btn().prop("disabled", false); }
      },
    });
    dialog.show();
    if (kind === "Cash") dialog.fields_dict.received.$input.on("input", () => dialog.$wrapper.find(".ua-pos-denom-total span").text(money(Math.max(0, flt(dialog.get_value("received")) - total))));
  }

  function cashMenu() {
    const dialog = new frappe.ui.Dialog({ title: "Операції з управлінською касою", fields: [{ fieldname: "actions", fieldtype: "HTML", options: `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><button class="btn btn-primary js-open">Відкрити зміну</button><button class="btn btn-danger js-close">Закрити зміну</button><button class="btn btn-default js-planned">Інкасація</button><button class="btn btn-default js-planned">Витрата з каси</button><button class="btn btn-default js-planned">Внесення готівки</button><button class="btn btn-default js-planned">Касовий звіт</button></div>` }] });
    dialog.show(); dialog.$wrapper.on("click", ".js-open", () => { dialog.hide(); openShift(); }); dialog.$wrapper.on("click", ".js-close", () => { dialog.hide(); closeShift(); }); dialog.$wrapper.on("click", ".js-planned", () => frappe.show_alert({ message: "Операція буде підключена на наступному етапі", indicator: "orange" }));
  }

  function fiscalMenu() {
    const configured = Boolean(state.session?.desk?.prro_cash_register);
    const dialog = new frappe.ui.Dialog({ title: "Фіскальний реєстратор", fields: [{ fieldname: "status", fieldtype: "HTML", options: `<div class="ua-pos-modal-note">ПРРО: <b>${esc(state.session?.desk?.prro_cash_register || "не налаштовано")}</b></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><button class="btn btn-default" ${configured ? "" : "disabled"}>Відкрити фіскальну зміну</button><button class="btn btn-default" ${configured ? "" : "disabled"}>X-звіт</button><button class="btn btn-default" ${configured ? "" : "disabled"}>Z-звіт</button><button class="btn btn-default" ${configured ? "" : "disabled"}>Службове внесення</button><button class="btn btn-default" ${configured ? "" : "disabled"}>Службова видача</button><button class="btn btn-default" ${configured ? "" : "disabled"}>Сторнування</button></div>${configured ? "" : "<p class='text-muted' style='margin-top:12px'>Прив’яжіть PRRO Cash Register у налаштуваннях каси.</p>"}` }] });
    dialog.show();
  }

  async function useIdentifiedCustomer(result) {
    if (result.status !== "Verified") return false;
    let customer = result.customer;
    if (!customer) {
      const customerName = await new Promise((resolve) => {
        frappe.prompt(
          { fieldname: "customer_name", fieldtype: "Data", label: "Ім’я покупця", reqd: 1 },
          (values) => resolve(values.customer_name),
          "Нового покупця підтверджено",
          "Створити картку",
        );
      });
      customer = await identificationApi("quick_create", {
        phone: result.phone,
        customer_name: customerName,
      });
    }
    renderOrder(
      await api("set_order_customer", {
        pos_session_token: state.token,
        order: state.order.name,
        customer: customer.name,
      }),
    );
    frappe.show_alert({ message: `Покупця ${customer.customer_name || customer.name} ідентифіковано`, indicator: "green" });
    return true;
  }

  function verificationDialog(request) {
    const isSms = request.channel === "SMS";
    const channelLabel = { SMS: "SMS", Telegram: "Telegram", Call: "контрольний дзвінок" }[request.channel];
    const link = request.deep_link
      ? `<p><a class="btn btn-primary" href="${esc(request.deep_link)}" target="_blank" rel="noopener">Відкрити Telegram-бота</a></p>`
      : "";
    const debug = request.debug_code
      ? `<div class="ua-pos-modal-note">Тестовий режим · код: <b style="font-size:18px">${esc(request.debug_code)}</b></div>`
      : "";
    const dialog = new frappe.ui.Dialog({
      title: `Ідентифікація · ${channelLabel}`,
      fields: [
        {
          fieldname: "info",
          fieldtype: "HTML",
          options: `<div class="ua-pos-modal-note"><b>${esc(request.phone)}</b><br>${esc(request.instructions)}</div>${link}${debug}<div class="js-id-status" style="margin:10px 0;color:#667085">Очікуємо підтвердження…</div>`,
        },
        ...(isSms
          ? [{ fieldname: "code", fieldtype: "Data", label: "Код із SMS", reqd: 1 }]
          : []),
      ],
      primary_action_label: isSms ? "Підтвердити код" : "Перевірити статус",
      primary_action: async (values) => {
        dialog.get_primary_btn().prop("disabled", true);
        try {
          const result = isSms
            ? await identificationApi("confirm", { request_id: request.request_id, code: values.code })
            : await identificationApi("get_status", { request_id: request.request_id });
          if (result.status === "Verified") {
            dialog.$wrapper.find(".js-id-status").html('<span style="color:#079455">● Покупця підтверджено</span>');
            if (await useIdentifiedCustomer(result)) dialog.hide();
          } else if (["Expired", "Failed", "Cancelled"].includes(result.status)) {
            dialog.$wrapper.find(".js-id-status").html(`<span style="color:#d92d20">● Запит завершено: ${esc(result.status)}</span>`);
          } else {
            dialog.$wrapper.find(".js-id-status").text("Підтвердження ще не отримано. Спробуйте перевірити ще раз.");
          }
        } finally {
          dialog.get_primary_btn().prop("disabled", false);
        }
      },
    });
    dialog.show();
    if (isSms) dialog.fields_dict.code.$input.focus();
  }

  async function identifyCustomer() {
    if (!state.session?.shift) return showNotice("Спочатку відкрийте управлінську зміну.", "error");
    if (!state.order || !canEditOrder()) await newOrder();
    let config;
    try {
      config = await identificationApi("get_config");
    } catch (error) {
      return showNotice("Модуль ідентифікації покупця ще не встановлено або не налаштовано.", "error");
    }
    if (!config.enabled || !config.channels?.length) {
      return showNotice("Увімкніть хоча б один канал у Customer Identification Settings.", "error");
    }
    const dialog = new frappe.ui.Dialog({
      title: "Ідентифікація покупця",
      fields: [
        { fieldname: "phone", fieldtype: "Data", label: "Номер телефону", reqd: 1, placeholder: "+380XXXXXXXXX" },
        { fieldname: "channel", fieldtype: "Select", label: "Канал підтвердження", options: config.channels.join("\n"), reqd: 1, default: config.channels[0] },
        { fieldname: "note", fieldtype: "HTML", options: '<div class="ua-pos-modal-note">Покупець підтверджує, що має доступ до вказаного номера. Код і технічні дані не зберігаються у відкритому вигляді.</div>' },
      ],
      primary_action_label: "Надіслати запит",
      primary_action: async (values) => {
        dialog.get_primary_btn().prop("disabled", true);
        try {
          const request = await identificationApi("begin", {
            channel: values.channel,
            phone: values.phone,
            reference_doctype: "POS Order",
            reference_name: state.order.name,
          });
          dialog.hide();
          verificationDialog(request);
        } finally {
          dialog.get_primary_btn().prop("disabled", false);
        }
      },
    });
    dialog.show();
    dialog.fields_dict.phone.$input.focus();
  }

  function planned(feature) { frappe.show_alert({ message: `${feature}: функція спроєктована і буде підключена наступним інкрементом`, indicator: "orange" }); }

  $root.on("click", ".ua-pos-login-button", login);
  $root.on("keydown", ".ua-pos-login-barcode", (event) => { if (event.key === "Enter") login(); });
  $root.on("click", ".js-logout", async () => { await api("logout", { pos_session_token: state.token }); sessionStorage.removeItem("ua_pos_token"); location.reload(); });
  $root.on("keydown", ".ua-pos-scan", async (event) => { if (event.key !== "Enter") return; const query = event.currentTarget.value.trim(); if (!query) return; event.currentTarget.value = ""; await scan(query); });
  $root.on("click", ".js-new-order", newOrder);
  $root.on("click", ".ua-pos-mode button", async function () { const mode = this.dataset.mode; if (mode === "Fiscal" && !state.session?.desk?.prro_cash_register) return showNotice("Фіскальний режим недоступний: для каси не налаштовано ПРРО.", "error"); state.saleMode = mode; if (canEditOrder()) renderOrder(await api("set_order_mode", { pos_session_token: state.token, order: state.order.name, fiscal_mode: mode })); else renderSession(); });
  $root.on("click", ".ua-pos-qty button", async function () { const rowName = $(this).closest("tr").data("row"); const row = state.order.items.find((item) => item.name === rowName); renderOrder(await api("set_item_qty", { pos_session_token: state.token, order: state.order.name, row_name: rowName, qty: flt(row.qty) + flt(this.dataset.delta) })); });
  $root.on("click", ".js-customer", () => { if (!canEditOrder()) return; frappe.prompt({ fieldname: "customer", fieldtype: "Link", options: "Customer", label: "Клієнт", reqd: 1, default: state.order.customer }, async (values) => renderOrder(await api("set_order_customer", { pos_session_token: state.token, order: state.order.name, customer: values.customer })), "Вибір клієнта", "Застосувати"); });
  $root.on("click", ".js-identify", identifyCustomer);
  $root.on("click", ".js-hold", async () => { if (state.order) renderOrder(await api("hold_order", { pos_session_token: state.token, order: state.order.name })); });
  $root.on("click", ".js-cancel", async () => { if (!state.order || !canEditOrder()) return; frappe.confirm("Скасувати поточний неоплачений чек?", async () => { await api("cancel_order", { pos_session_token: state.token, order: state.order.name }); renderOrder(null); }); });
  $root.on("click", ".js-pay-cash", () => paymentDialog("Cash"));
  $root.on("click", ".js-pay-card", () => paymentDialog("Card"));
  $root.on("click", ".js-cash-menu", cashMenu);
  $root.on("click", ".js-fiscal-menu", fiscalMenu);
  $root.on("click", ".js-stock", () => planned("Пошук по складу"));
  $root.on("click", ".js-reports", () => planned("Касові та товарні звіти"));
  $root.on("click", ".js-return", () => frappe.prompt({ fieldname: "token", fieldtype: "Data", label: "Штрихкод первинного чека", reqd: 1 }, async (values) => { const order = await api("lookup_return", { pos_session_token: state.token, token: values.token }); frappe.msgprint({ title: "Первинний продаж знайдено", message: `${esc(order.name)} · ${money(order.grand_total)} грн · ${esc(order.customer)}`, indicator: "green" }); }, "Повернення", "Знайти продаж"));

  $(document).off("keydown.ua_pos").on("keydown.ua_pos", (event) => {
    if ($(event.target).is("input,textarea,select") && event.key !== "F2") return;
    const actions = { F2: () => $root.find(".ua-pos-scan").focus(), F3: () => $root.find(".js-stock").click(), F4: () => $root.find(".js-customer").first().click(), F5: () => $root.find(".js-identify").first().click(), F7: () => $root.find(".js-hold").click(), F8: () => $root.find(".js-return").click(), F9: () => $root.find(".js-pay-cash").first().click() };
    if (actions[event.key]) { event.preventDefault(); actions[event.key](); }
    if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "s") { event.preventDefault(); openShift(); }
    if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "c") { event.preventDefault(); closeShift(); }
  });

  updateClock(); state.clock = setInterval(updateClock, 1000); refreshSession();
};

frappe.pages["ua-pos"].on_page_hide = function (wrapper) {
  $(document).off("keydown.ua_pos");
  if (wrapper.uaPosState?.clock) clearInterval(wrapper.uaPosState.clock);
};
