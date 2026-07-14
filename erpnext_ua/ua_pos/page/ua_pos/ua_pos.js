try {
  localStorage.removeItem("_page:ua-pos");
} catch (error) {
  console.warn("UA POS page cache could not be cleared", error);
}

frappe.pages["ua-pos"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: "Каса", single_column: true });
  const layoutDefaults = {
    showImage: true,
    columns: {
      item: { visible: true, width: 300 },
      barcode: { visible: true, width: 130 },
      qty: { visible: true, width: 125 },
      uom: { visible: true, width: 65 },
      rate: { visible: true, width: 100 },
      discount: { visible: true, width: 90 },
      amount: { visible: true, width: 110 },
      tracking: { visible: true, width: 150 },
      status: { visible: false, width: 105 },
    },
  };
  const loadLayout = () => {
    try {
      const saved = JSON.parse(localStorage.getItem("ua_pos_layout") || "{}");
      return {
        showImage: saved.showImage ?? layoutDefaults.showImage,
        columns: Object.fromEntries(Object.entries(layoutDefaults.columns).map(([key, value]) => [key, { ...value, ...(saved.columns?.[key] || {}) }])),
      };
    } catch (error) {
      console.warn("UA POS layout could not be loaded", error);
      return JSON.parse(JSON.stringify(layoutDefaults));
    }
  };
  const state = {
    token: sessionStorage.getItem("ua_pos_token"),
    session: null,
    order: null,
    saleMode: "Fiscal",
    clock: null,
    layout: loadLayout(),
    lastItem: null,
    birthdayPromptKey: null,
  };
  wrapper.uaPosState = state;

  const statusLabels = {
    Building: "Формування чека",
    Held: "Відкладено",
    "Invoice Draft": "Створено рахунок",
    "Awaiting Payment": "Очікує оплату",
    "Payment In Progress": "Оплата виконується",
    "Payment Unknown": "Статус оплати невідомий",
    Paid: "Оплачено",
    Posting: "Проведення",
    Posted: "Проведено",
    "Fiscal Pending": "Очікує фіскалізацію",
    Printing: "Друк чека",
    Completed: "Завершено",
    "Completed Print Error": "Помилка друку",
    "Manual Review": "Потрібна перевірка",
    Cancelled: "Скасовано",
  };
  const esc = (value) => frappe.utils.escape_html(String(value ?? ""));
  const money = (value) => format_number(flt(value || 0), null, 2);
  const idem = () => {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    const random = globalThis.crypto?.getRandomValues
      ? Array.from(globalThis.crypto.getRandomValues(new Uint32Array(4)), (value) => value.toString(16)).join("")
      : `${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`;
    return `${Date.now().toString(36)}-${random}`;
  };
  const deviceToken = () => {
    const key = "ua_pos_device_token";
    let token = localStorage.getItem(key);
    if (!token) {
      token = idem();
      localStorage.setItem(key, token);
    }
    return token;
  };
  const api = (method, args = {}) =>
    frappe.call({ method: `erpnext_ua.ua_pos.api.${method}`, args }).then((response) => response.message);
  const serverErrorMessage = (error) => {
    const raw = error?.responseJSON?._server_messages;
    if (raw) {
      try {
        const messages = JSON.parse(raw).map((row) => {
          try {
            const parsed = typeof row === "string" ? JSON.parse(row) : row;
            return parsed?.message || String(parsed || "");
          } catch (parseError) {
            return String(row || "");
          }
        }).filter(Boolean);
        if (messages.length) return messages.join("\n");
      } catch (parseError) {
        console.warn("UA POS could not parse server error", parseError);
      }
    }
    return error?.message || "Операцію не виконано. Перевірте журнал помилок ПРРО.";
  };
  const identificationApi = (method, args = {}) =>
    frappe.call({ method: `ukrainian_integrations.customer_identification.service.${method}`, args }).then((response) => response.message);

  const styles = `<style id="ua-pos-v2-styles">
    .layout-main-section-wrapper{margin-bottom:0!important}.layout-main-section{padding:0!important}.page-head{display:none!important}
    .ua-pos{--ink:#172033;--muted:#667085;--line:#dfe4ec;--panel:#fff;--bg:#f3f6fa;--blue:#2563eb;--blue2:#1d4ed8;--green:#079455;--amber:#dc6803;--red:#d92d20;min-height:calc(100vh - 48px);background:var(--bg);color:var(--ink);font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
    .ua-pos *{box-sizing:border-box}.ua-pos button,.ua-pos input,.ua-pos select{font:inherit}.ua-pos-login-screen{min-height:calc(100vh - 48px);display:grid;place-items:center;padding:32px;background:radial-gradient(circle at 15% 10%,#dbeafe 0,transparent 34%),radial-gradient(circle at 85% 85%,#d1fae5 0,transparent 30%),#f8fafc}
    .ua-pos-login-card{width:min(520px,100%);background:#fff;border:1px solid #e4e7ec;border-radius:20px;box-shadow:0 24px 70px rgba(16,24,40,.14);padding:36px}.ua-pos-brand{display:flex;align-items:center;gap:12px}.ua-pos-logo{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#2563eb,#0f766e);display:grid;place-items:center;color:#fff;font-weight:800;font-size:20px}.ua-pos-brand strong{font-size:20px}.ua-pos-brand small{display:block;color:var(--muted);margin-top:2px}.ua-pos-login-card h1{font-size:27px;margin:32px 0 6px}.ua-pos-login-card>p{color:var(--muted);margin:0 0 24px}.ua-pos-field{margin:14px 0}.ua-pos-field label{display:block;font-weight:650;font-size:13px;margin-bottom:7px}.ua-pos-field input,.ua-pos-field select{width:100%;height:48px;border:1px solid #cfd6e2;border-radius:10px;padding:0 14px;outline:0;background:#fff}.ua-pos-field input:focus,.ua-pos-field select:focus{border-color:var(--blue);box-shadow:0 0 0 3px #dbeafe}.ua-pos-login-button{width:100%;height:50px;border:0;border-radius:10px;background:var(--blue);color:#fff;font-weight:750;margin-top:10px}.ua-pos-login-note{margin-top:18px;padding:11px 13px;background:#f8fafc;border-radius:9px;color:var(--muted);font-size:12px}
    .ua-pos-workspace{display:none;min-height:calc(100vh - 48px)}.ua-pos-topbar{height:54px;background:#111827;color:#fff;display:flex;align-items:center;gap:18px;padding:0 20px;position:sticky;top:0;z-index:20}.ua-pos-topbar .ua-pos-brand{margin-right:auto}.ua-pos-topbar .ua-pos-logo{width:34px;height:34px;font-size:15px}.ua-pos-topbar .ua-pos-brand strong{font-size:16px}.ua-pos-statuses,.ua-pos-user-details{display:none}.ua-pos-chip{height:34px;display:flex;align-items:center;gap:7px;padding:0 11px;border:1px solid #344054;border-radius:8px;color:#d0d5dd;font-size:12px;white-space:nowrap}.ua-pos-chip b{color:#fff;font-weight:650}.ua-pos-dot{width:8px;height:8px;border-radius:50%;background:#98a2b3}.ua-pos-dot.ok{background:#32d583}.ua-pos-dot.warn{background:#fdb022}.ua-pos-top-actions{display:flex;align-items:center;gap:4px}.ua-pos-icon-button{border:0;background:transparent;color:#d0d5dd;font-size:18px;padding:8px;border-radius:7px}.ua-pos-icon-button:hover{background:#344054;color:#fff}
    .ua-pos-command{padding:14px 18px 10px;background:#fff;border-bottom:1px solid var(--line)}.ua-pos-command-top{display:flex;gap:10px;align-items:stretch}.ua-pos-search-wrap{position:relative;flex:1}.ua-pos-search-icon{position:absolute;left:15px;top:13px;font-size:20px;color:var(--blue)}.ua-pos-scan{height:50px;width:100%;border:2px solid #b8c5d8;border-radius:10px;padding:0 135px 0 46px;font-size:17px;font-weight:600;outline:0}.ua-pos-scan:focus{border-color:var(--blue);box-shadow:0 0 0 3px #dbeafe}.ua-pos-keyhint{position:absolute;right:12px;top:12px;border:1px solid #d0d5dd;background:#f9fafb;border-radius:6px;padding:4px 8px;font-size:11px;color:var(--muted)}.ua-pos-mode{display:flex;border:1px solid #d0d5dd;border-radius:10px;padding:4px;background:#f8fafc}.ua-pos-mode button{border:0;background:transparent;border-radius:7px;padding:0 14px;font-size:12px;font-weight:700;color:var(--muted)}.ua-pos-mode button.active{background:#fff;color:var(--blue);box-shadow:0 1px 4px rgba(16,24,40,.12)}.ua-pos-mode button.fiscal.active{color:#b54708;background:#fffaeb}.ua-pos-command-actions{display:flex;gap:7px;margin-top:10px;overflow-x:auto;padding-bottom:1px}.ua-pos-action{height:38px;border:1px solid #d0d5dd;border-radius:8px;background:#fff;padding:0 12px;display:flex;align-items:center;gap:7px;color:#344054;font-size:12px;font-weight:650;white-space:nowrap}.ua-pos-action:hover{background:#f9fafb;border-color:#98a2b3}.ua-pos-action.primary{background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8}.ua-pos-action.danger{color:#b42318}.ua-pos-action[disabled]{opacity:.45;cursor:not-allowed}.ua-pos-shortcut{font-size:10px;color:#98a2b3;border-left:1px solid #d0d5dd;padding-left:7px}
    .ua-pos-alert{display:none;margin:12px 18px 0;padding:10px 13px;border-radius:8px;background:#fffaeb;border:1px solid #fedf89;color:#93370d;font-size:13px}.ua-pos-main{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:14px;padding:14px 18px 18px;height:calc(100vh - 192px)}.ua-pos-panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 2px rgba(16,24,40,.04);overflow:hidden}.ua-pos-cart-panel{display:flex;flex-direction:column;min-width:0}.ua-pos-sale-info{min-height:62px;display:flex;align-items:center;gap:22px;padding:9px 14px;border-bottom:1px solid var(--line);background:#fbfcfe}.ua-pos-sale-info-item{min-width:0}.ua-pos-sale-info-item label{display:block;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}.ua-pos-sale-info-item strong{display:block;font-size:13px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ua-pos-customer-button{margin-left:auto;border:1px solid #d0d5dd;border-radius:8px;background:#fff;height:36px;padding:0 12px;color:#344054;font-weight:650;font-size:12px}
    .ua-pos-table-wrap{flex:1;overflow:auto;position:relative}.ua-pos-table{border-collapse:separate;border-spacing:0;width:100%;min-width:900px;table-layout:fixed;font-size:12px}.ua-pos-table th{position:sticky;top:0;z-index:2;background:#f2f4f7;color:#475467;text-align:left;padding:9px 10px;border-bottom:1px solid #d0d5dd;font-weight:700;white-space:nowrap}.ua-pos-table td{padding:9px 10px;border-bottom:1px solid #eaecf0;vertical-align:middle;overflow:hidden;text-overflow:ellipsis}.ua-pos-table tbody tr:hover{background:#f8fbff}.ua-pos-table .num{text-align:right;font-variant-numeric:tabular-nums}.ua-pos-item-name{font-weight:700;color:#1d2939;max-width:320px}.ua-pos-item-code{font-size:10px;color:var(--muted);margin-top:2px}.ua-pos-qty{display:inline-flex;align-items:center;border:1px solid #d0d5dd;border-radius:7px;overflow:hidden}.ua-pos-qty button{width:26px;height:26px;border:0;background:#f9fafb;color:#344054}.ua-pos-qty span{min-width:38px;text-align:center;font-weight:700}.ua-pos-empty{position:absolute;inset:44px 0 0;display:grid;place-items:center;text-align:center;color:var(--muted)}.ua-pos-empty-icon{font-size:42px;opacity:.35}.ua-pos-empty strong{display:block;color:#475467;font-size:16px;margin:8px}.ua-pos-cart-footer{height:42px;border-top:1px solid var(--line);display:flex;align-items:center;gap:20px;padding:0 14px;background:#fbfcfe;color:var(--muted);font-size:11px}.ua-pos-cart-footer b{color:#344054}
    .ua-pos-summary{display:flex;flex-direction:column}.ua-pos-summary-head{padding:15px 16px;border-bottom:1px solid var(--line)}.ua-pos-summary-head span{font-size:11px;color:var(--muted)}.ua-pos-summary-head strong{display:block;font-size:15px;margin-top:3px}.ua-pos-order-badge{display:inline-flex!important;width:auto;margin-top:8px!important;padding:3px 7px;border-radius:5px;background:#f2f4f7;color:#475467!important;font-size:10px!important}.ua-pos-product-preview{display:none;align-items:center;gap:12px;min-height:112px;padding:12px 16px;border-bottom:1px solid var(--line);background:#fbfcfe}.ua-pos-product-preview img{width:88px;height:88px;border-radius:9px;border:1px solid var(--line);object-fit:contain;background:#fff}.ua-pos-product-preview strong{display:block;font-size:13px;line-height:1.25}.ua-pos-product-preview span{display:block;margin-top:5px;color:var(--muted);font-size:11px}.ua-pos-totals{padding:14px 16px}.ua-pos-total-row{display:flex;justify-content:space-between;align-items:center;margin:9px 0;color:#475467;font-size:13px}.ua-pos-total-row strong{color:#1d2939;font-variant-numeric:tabular-nums}.ua-pos-total-row.discount strong{color:var(--green)}.ua-pos-due{margin-top:auto;padding:18px 16px 16px;border-top:1px solid var(--line);background:#f8fafc}.ua-pos-due-label{font-size:12px;color:var(--muted);font-weight:650}.ua-pos-due-value{font-size:38px;line-height:1.1;font-weight:850;letter-spacing:-.04em;margin:5px 0 15px;color:#101828;font-variant-numeric:tabular-nums}.ua-pos-pay-main{height:54px;width:100%;border:0;border-radius:10px;background:var(--green);color:#fff;font-size:16px;font-weight:800}.ua-pos-pay-main:disabled{background:#98a2b3}.ua-pos-pay-split{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}.ua-pos-pay-split button{height:42px;border:1px solid #d0d5dd;border-radius:8px;background:#fff;color:#344054;font-weight:700;font-size:12px}.ua-pos-pay-split button.card{color:#1d4ed8;border-color:#bfdbfe;background:#eff6ff}.ua-pos-footer-status{display:flex;gap:13px;padding:8px 16px;border-top:1px solid var(--line);font-size:10px;color:var(--muted)}
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
          <div class="ua-pos-field"><label>Каса</label><select class="ua-pos-login-desk" disabled><option value="">Завантаження кас…</option></select></div>
          <div class="ua-pos-field"><label>Штрихкод працівника</label><input class="ua-pos-login-barcode" type="password" autocomplete="off" placeholder="Відскануйте або введіть код"></div>
          <button class="ua-pos-login-button">Увійти до каси</button>
          <div class="ua-pos-login-note">Тестовий касир: <b>POS-TEST-CASHIER</b>. У продуктивному режимі використовується персональна картка працівника.</div>
        </div>
      </section>
      <section class="ua-pos-workspace">
        <header class="ua-pos-topbar">
          <div class="ua-pos-brand"><div class="ua-pos-logo">UA</div><div><strong>Каса</strong></div></div>
          <div class="ua-pos-statuses">
            <div class="ua-pos-chip"><i class="ua-pos-dot js-shift-dot"></i><span>Зміна</span><b class="js-shift">закрита</b></div>
            <div class="ua-pos-chip"><i class="ua-pos-dot ok"></i><span>Склад</span><b class="js-warehouse">—</b></div>
            <div class="ua-pos-chip"><i class="ua-pos-dot js-prro-dot"></i><span>ПРРО</span><b class="js-prro">не налаштовано</b></div>
            <div class="ua-pos-chip"><i class="ua-pos-dot js-terminal-dot"></i><span>Термінал</span><b class="js-terminal">не налаштовано</b></div>
          </div>
          <div class="ua-pos-user-details"><span class="js-desk">—</span><strong class="js-employee">—</strong><span class="js-clock"></span><span class="js-avatar">К</span></div>
          <div class="ua-pos-top-actions"><button class="ua-pos-icon-button js-layout" title="Налаштувати вигляд">⚙</button><button class="ua-pos-icon-button js-service-info" title="Службова інформація">ⓘ</button><button class="ua-pos-icon-button js-logout" title="Вийти">↪</button></div>
        </header>
        <section class="ua-pos-command">
          <div class="ua-pos-command-top">
            <div class="ua-pos-search-wrap"><span class="ua-pos-search-icon">⌕</span><input class="ua-pos-scan" autocomplete="off" placeholder="Штрихкод, артикул або назва товару"><span class="ua-pos-keyhint">F2 · Enter</span></div>
            <div class="ua-pos-mode"><button data-mode="Non Fiscal">Без фіскалізації</button><button data-mode="Fiscal" class="fiscal active">Фіскальний продаж</button></div>
          </div>
          <div class="ua-pos-command-actions">
            <button class="ua-pos-action primary js-new-order">＋ Новий чек</button>
            <button class="ua-pos-action js-stock">⌕ Пошук по складу <span class="ua-pos-shortcut">F3</span></button>
            <button class="ua-pos-action js-customer">♙ Клієнт <span class="ua-pos-shortcut">F4</span></button>
            <button class="ua-pos-action primary js-identify">◎ Ідентифікувати <span class="ua-pos-shortcut">F5</span></button>
            <button class="ua-pos-action js-create-invoice">▧ Створити рахунок</button>
            <button class="ua-pos-action js-discount">% Знижка <span class="ua-pos-shortcut">F6</span></button>
            <button class="ua-pos-action js-hold">◫ Відкласти <span class="ua-pos-shortcut">F7</span></button>
            <button class="ua-pos-action js-orders">▱ Відкладені чеки</button>
            <button class="ua-pos-action js-return">↩ Повернення <span class="ua-pos-shortcut">F8</span></button>
            <button class="ua-pos-action js-cash-menu">₴ Операції з касою</button>
            <button class="ua-pos-action js-fiscal-menu">▣ Фіскальне меню</button>
            <button class="ua-pos-action primary js-retry-fiscal" style="display:none">↻ Відновити фіскалізацію</button>
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
              <table class="ua-pos-table"><thead><tr><th data-col="item">Товар / артикул</th><th data-col="barcode">Штрихкод</th><th data-col="qty" class="num">Кількість</th><th data-col="uom">Од.</th><th data-col="rate" class="num">Ціна</th><th data-col="discount" class="num">Знижка</th><th data-col="amount" class="num">Сума</th><th data-col="tracking">Партія / серійний №</th><th data-col="status">Перевірка</th></tr></thead><tbody class="js-cart-body"></tbody></table>
              <div class="ua-pos-empty js-empty"><div><div class="ua-pos-empty-icon">▦</div><strong>Чек порожній</strong><span>Відскануйте штрихкод або введіть артикул у полі зверху</span></div></div>
            </div>
            <div class="ua-pos-cart-footer"><span>Артикулів: <b class="js-lines">0</b></span><span>Кількість: <b class="js-qty">0</b></span><span>Гарячі клавіші: <b>F2 пошук · F4 клієнт · F8 повернення · F9 оплатити</b></span></div>
          </section>
          <aside class="ua-pos-panel ua-pos-summary">
            <div class="ua-pos-summary-head"><span>Поточний документ</span><strong class="js-order-name">Чек ще не створено</strong><span class="ua-pos-order-badge js-order-badge">ГОТОВО ДО РОБОТИ</span></div>
            <div class="ua-pos-product-preview"><img class="js-product-image" alt="Фото товару"><div><strong class="js-product-name"></strong><span class="js-product-code"></span></div></div>
            <div class="ua-pos-totals">
              <div class="ua-pos-total-row"><span>Повна сума</span><strong><span class="js-net">0,00</span> грн</strong></div>
              <div class="ua-pos-total-row discount"><span>Знижка</span><strong>− <span class="js-discount">0,00</span> грн</strong></div>
              <div class="ua-pos-total-row"><span>Бонуси</span><strong>0,00 грн</strong></div>
              <div class="ua-pos-total-row"><span>Оплачено</span><strong><span class="js-paid">0,00</span> грн</strong></div>
              <div class="ua-pos-total-row js-cash-received-row" style="display:none"><span>Отримано готівкою</span><strong><span class="js-cash-received">0,00</span> грн</strong></div>
              <div class="ua-pos-total-row"><span>Решта</span><strong><span class="js-change">0,00</span> грн</strong></div>
            </div>
            <div class="ua-pos-due"><div class="ua-pos-due-label">Сума до оплати</div><div class="ua-pos-due-value"><span class="js-total">0,00</span> <small>грн</small></div><button class="ua-pos-pay-main js-pay-cash" disabled>Оплатити · F9</button><div class="ua-pos-pay-split"><button class="js-pay-cash" disabled>Готівка</button><button class="card js-pay-card" disabled>Банківська картка</button><button class="js-pay-mixed" disabled>Змішана оплата</button><button class="js-print" disabled>Друк чека</button></div></div>
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

  const layoutLabels = {
    item: "Товар / артикул",
    barcode: "Штрихкод",
    qty: "Кількість",
    uom: "Одиниця виміру",
    rate: "Ціна",
    discount: "Знижка",
    amount: "Сума",
    tracking: "Партія / серійний номер",
    status: "Перевірка",
  };

  function applyLayout() {
    let minimumWidth = 0;
    Object.entries(state.layout.columns).forEach(([key, config]) => {
      const width = Math.max(55, Math.min(600, cint(config.width || layoutDefaults.columns[key].width)));
      $root.find(`[data-col="${key}"]`).toggle(Boolean(config.visible)).css({ width: `${width}px`, minWidth: `${width}px`, maxWidth: `${width}px` });
      if (config.visible) minimumWidth += width;
    });
    $root.find(".ua-pos-table").css("min-width", `${Math.max(550, minimumWidth)}px`);
    renderProductPreview();
  }

  function renderProductPreview() {
    const items = state.order?.items || [];
    const item = items.find((row) => row.name === state.lastItem?.name) || items.at(-1);
    const visible = Boolean(state.layout.showImage && item?.image);
    $root.find(".ua-pos-product-preview").css("display", visible ? "flex" : "none");
    if (!visible) return;
    $root.find(".js-product-image").attr("src", item.image);
    $root.find(".js-product-name").text(item.item_name || item.item_code);
    $root.find(".js-product-code").text(item.item_code || "");
  }

  function layoutDialog() {
    const fields = [{ fieldname: "show_image", fieldtype: "Check", label: "Показувати фото останнього вибраного товару", default: state.layout.showImage ? 1 : 0 }];
    Object.entries(layoutLabels).forEach(([key, label]) => {
      fields.push(
        { fieldname: `${key}_visible`, fieldtype: "Check", label: `Показувати «${label}»`, default: state.layout.columns[key].visible ? 1 : 0 },
        { fieldname: `${key}_width`, fieldtype: "Int", label: `Ширина «${label}», px`, default: state.layout.columns[key].width, depends_on: `eval:doc.${key}_visible` },
      );
    });
    const dialog = new frappe.ui.Dialog({
      title: "Вигляд вікна касира",
      size: "large",
      fields,
      primary_action_label: "Зберегти",
      primary_action: (values) => {
        state.layout.showImage = Boolean(values.show_image);
        Object.keys(layoutLabels).forEach((key) => {
          state.layout.columns[key].visible = Boolean(values[`${key}_visible`]);
          state.layout.columns[key].width = Math.max(55, Math.min(600, cint(values[`${key}_width`] || layoutDefaults.columns[key].width)));
        });
        localStorage.setItem("ua_pos_layout", JSON.stringify(state.layout));
        applyLayout();
        dialog.hide();
        frappe.show_alert({ message: "Вигляд каси збережено для цього пристрою", indicator: "green" });
      },
    });
    dialog.show();
  }

  function serviceInfoDialog() {
    const session = state.session || {};
    const desk = session.desk || {};
    const value = (label, content) => `<div style="padding:8px 0;border-bottom:1px solid #eaecf0"><span style="display:block;color:#667085;font-size:11px">${label}</span><b>${esc(content || "не налаштовано")}</b></div>`;
    frappe.msgprint({
      title: "Службова інформація каси",
      indicator: session.shift ? "green" : "orange",
      message: `${value("Каса", session.cash_desk)}${value("Зміна", session.shift || "закрита")}${value("Склад", desk.warehouse)}${value("ПРРО", desk.prro_cash_register)}${value("Банківський термінал", desk.terminal)}${value("Працівник", session.employee_name || session.employee)}${value("Дата й час", new Intl.DateTimeFormat("uk-UA", { dateStyle: "short", timeStyle: "medium" }).format(new Date()))}`,
    });
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
    state.saleMode = desk.prro_cash_register ? "Fiscal" : "Non Fiscal";
    $root.find('.ua-pos-mode button[data-mode="Non Fiscal"]').prop(
      "disabled",
      !["Senior Cashier", "Manager"].includes(session.access_role)
    );
    $root.find(".ua-pos-mode button").removeClass("active").filter(`[data-mode="${state.saleMode}"]`).addClass("active");
    $root.find(".js-footer-mode").text(state.saleMode === "Fiscal" ? "● фіскальний режим" : "○ без фіскалізації");
    $root.find(".js-new-order").prop("disabled", !session.shift);
    if (!session.shift) showNotice("Управлінська зміна закрита. Відкрийте зміну через «Операції з касою», щоб почати продаж.");
    else clearNotice();
  }

  function renderOrder(order) {
    state.order = order || null;
    const items = order?.items || [];
    if (!order) {
      state.lastItem = null;
      state.birthdayPromptKey = null;
    }
    else state.lastItem = items.find((item) => item.name === state.lastItem?.name) || items.at(-1) || null;
    const editable = canEditOrder();
    $root.find(".js-empty").toggle(items.length === 0);
    $root.find(".js-order-name").text(order ? `${order.order_type === "Return" ? "Повернення" : "Чек"} ${order.name}` : "Чек ще не створено");
    $root.find(".js-order-status").text(order ? statusLabels[order.status] || order.status : "Новий чек");
    $root.find(".js-order-badge").text(order ? (statusLabels[order.status] || order.status).toUpperCase() : "ГОТОВО ДО РОБОТИ");
    $root.find(".js-customer-name").text(order?.customer || "Роздрібний покупець");
    $root.find(".js-fop").text(order?.items?.find((item) => item.fop_profile)?.fop_profile || (order?.fiscal_mode === "Fiscal" ? "Визначиться під час фіскалізації" : "Не застосовується"));
    $root.find(".js-net").text(money(order?.net_total));
    $root.find(".js-discount").text(money(order?.discount_total));
    $root.find(".js-paid").text(money(order?.paid_total));
    const confirmedCash = (order?.payments_plan || []).filter((payment) => payment.kind === "Cash" && payment.status === "Confirmed");
    const cashReceived = confirmedCash.reduce((sum, payment) => sum + flt(payment.tendered_amount || payment.amount), 0);
    $root.find(".js-cash-received-row").toggle(confirmedCash.length > 0);
    $root.find(".js-cash-received").text(money(cashReceived));
    $root.find(".js-change").text(money(order?.change_amount));
    $root.find(".js-total").text(money(order?.grand_total));
    $root.find(".js-lines").text(items.length);
    $root.find(".js-qty").text(money(items.reduce((sum, item) => sum + flt(item.qty), 0)));
    $root.find(".js-cart-body").html(items.map((item) => `
      <tr data-row="${esc(item.name)}"><td data-col="item"><div class="ua-pos-item-name">${esc(item.item_name || item.item_code)}</div><div class="ua-pos-item-code">${esc(item.item_code)}</div></td><td data-col="barcode">${esc(item.barcode || "—")}</td><td data-col="qty" class="num"><div class="ua-pos-qty"><button data-delta="-1" ${editable && order?.order_type !== "Return" ? "" : "disabled"}>−</button><span>${esc(item.qty)}</span><button data-delta="1" ${editable && order?.order_type !== "Return" ? "" : "disabled"}>＋</button></div></td><td data-col="uom">${esc(item.uom || "—")}</td><td data-col="rate" class="num">${money(item.rate)}</td><td data-col="discount" class="num">${money(item.discount_amount)}</td><td data-col="amount" class="num"><b>${money(item.amount)}</b></td><td data-col="tracking"><button class="btn btn-xs btn-default js-track-item" ${editable && order?.order_type !== "Return" ? "" : "disabled"}>${esc(item.batch_no || item.serial_no || "Вказати")}</button></td><td data-col="status"><span style="color:#079455">● Готово</span></td></tr>`).join(""));
    const payable = Boolean(order && items.length && ["Building", "Awaiting Payment"].includes(order.status) && state.session?.shift);
    $root.find(".js-pay-cash").prop("disabled", !payable);
    $root.find(".js-pay-card").prop("disabled", !payable || !state.session?.desk?.terminal);
    $root.find(".js-pay-mixed").prop("disabled", !payable || order?.fiscal_mode !== "Fiscal");
    $root.find(".js-print").prop("disabled", !order || !["Completed", "Completed Print Error"].includes(order.status));
    $root.find(".js-retry-fiscal").toggle(Boolean(order && ["Fiscal Pending", "Posted", "Manual Review"].includes(order.status)));
    $root.find(".js-hold").prop("disabled", !order || !["Building", "Held"].includes(order.status));
    const customerActionAvailable = Boolean(state.session?.shift && (!order || (editable && order?.order_type !== "Return")));
    $root.find(".js-customer,.js-identify").prop("disabled", !customerActionAvailable);
    $root.find(".js-discount,.js-create-invoice,.js-cancel").prop("disabled", !editable || order?.order_type === "Return");
    $root.find(".js-hold").html(order?.status === "Held" ? "▶ Повернути чек <span class=\"ua-pos-shortcut\">F7</span>" : "◫ Відкласти <span class=\"ua-pos-shortcut\">F7</span>");
    if (order?.fiscal_mode) {
      state.saleMode = order.fiscal_mode;
      $root.find(".ua-pos-mode button").removeClass("active").filter(`[data-mode="${state.saleMode}"]`).addClass("active");
    }
    applyLayout();
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

  async function loadCashDesks() {
    const $select = $root.find(".ua-pos-login-desk");
    try {
      const desks = await api("list_cash_desks");
      $select.empty().append('<option value="">Оберіть касу</option>');
      desks.forEach((desk) => {
        const label = `${desk.desk_name || desk.name} — ${desk.company} / ${desk.warehouse}`;
        $("<option>").val(desk.name).text(label).appendTo($select);
      });
      const saved = localStorage.getItem("ua_pos_cash_desk") || "";
      if (saved && desks.some((desk) => desk.name === saved)) $select.val(saved);
      else if (desks.length === 1) $select.val(desks[0].name);
      $select.prop("disabled", false);
    } catch (error) {
      $select.empty().append('<option value="">Не вдалося завантажити каси</option>').prop("disabled", true);
    }
  }

  async function login() {
    const $barcode = $root.find(".ua-pos-login-barcode");
    try {
      const cashDesk = ($root.find(".ua-pos-login-desk").val() || "").trim();
      const result = await api("login_by_barcode", { cash_desk: cashDesk, barcode: ($barcode.val() || "").trim(), device_token: deviceToken() });
      state.token = result.session_token;
      localStorage.setItem("ua_pos_cash_desk", cashDesk);
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
    if (state.order?.status === "Building" && state.order.items?.length) {
      renderOrder(await api("hold_order", { pos_session_token: state.token, order: state.order.name }));
    }
    renderOrder(await api("create_order", { pos_session_token: state.token, idem_key: idem(), fiscal_mode: state.saleMode }));
  }

  async function scan(query) {
    if (!state.session?.shift) return showNotice("Продаж неможливий: управлінська зміна закрита.", "error");
    if (!state.order || !canEditOrder()) await newOrder();
    const order = await api("scan_item", { pos_session_token: state.token, order: state.order.name, query });
    state.lastItem = (order.items || []).find((item) => [item.item_code, item.barcode].includes(query)) || order.items?.at(-1) || null;
    renderOrder(order);
    await maybeOfferBirthday();
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
          const completed = await api("checkout_start", { pos_session_token: state.token, order: state.order.name, payments: JSON.stringify([{ mode_of_payment: values.mode, kind, amount: total, tendered_amount: kind === "Cash" ? flt(values.received) : total, currency: "UAH" }]), idem_key: idem() });
          renderOrder(completed); dialog.hide();
          if (completed.status === "Payment Unknown") resolveUnknownPayment(completed);
          frappe.show_alert({ message: `${completed.name}: ${statusLabels[completed.status] || completed.status}`, indicator: completed.status === "Completed" ? "green" : "orange" });
        } finally { dialog.get_primary_btn().prop("disabled", false); }
      },
    });
    dialog.show();
    if (kind === "Cash") dialog.fields_dict.received.$input.on("input", () => dialog.$wrapper.find(".ua-pos-denom-total span").text(money(Math.max(0, flt(dialog.get_value("received")) - total))));
  }

  function resolveUnknownPayment(order) {
    const attempt = (order.payments_plan || []).find((row) => row.kind === "Card" && row.payment_attempt)?.payment_attempt;
    if (!attempt) return;
    const dialog = new frappe.ui.Dialog({
      title: "Невідомий стан оплати",
      fields: [{ fieldname: "info", fieldtype: "HTML", options: '<div class="ua-pos-modal-note">Не повторюйте оплату. Система перевірить стан попередньої операції за її ідентифікатором.</div><div class="js-terminal-state text-muted">Очікуємо перевірку…</div>' }],
      primary_action_label: "Перевірити стан термінала",
      primary_action: async () => {
        const result = await api("card_status", { pos_session_token: state.token, attempt });
        renderOrder(result.order);
        dialog.$wrapper.find(".js-terminal-state").text(`Стан: ${statusLabels[result.order.status] || result.order.status}`);
        if (result.order.status !== "Payment Unknown") dialog.hide();
      },
    });
    dialog.show();
  }

  function cashOperationDialog(movementType, title) {
    const dialog = new frappe.ui.Dialog({
      title,
      fields: [
        { fieldname: "amount", fieldtype: "Currency", label: "Сума, грн", reqd: 1 },
        { fieldname: "notes", fieldtype: "Small Text", label: "Підстава / коментар", reqd: movementType !== "Cash In" },
      ],
      primary_action_label: "Провести операцію",
      primary_action: async (values) => {
        const result = await api("cash_operation", { pos_session_token: state.token, movement_type: movementType, amount: values.amount, notes: values.notes || "", idem_key: idem() });
        dialog.hide();
        frappe.show_alert({ message: `${title}: ${money(values.amount)} грн · залишок ${money(result.cash_balance)} грн`, indicator: "green" });
      },
    });
    dialog.show();
    dialog.fields_dict.amount.$input.focus();
  }

  function cashMenu() {
    const opened = Boolean(state.session?.shift);
    const dialog = new frappe.ui.Dialog({ title: "Операції з управлінською касою", fields: [{ fieldname: "actions", fieldtype: "HTML", options: `<div class="ua-pos-modal-note">Управлінська зміна: <b>${opened ? esc(state.session.shift) : "закрита"}</b></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><button class="btn btn-primary js-open" ${opened ? "disabled" : ""}>Відкрити зміну</button><button class="btn btn-danger js-close" ${opened ? "" : "disabled"}>Закрити зміну</button><button class="btn btn-default js-incassation" ${opened ? "" : "disabled"}>Інкасація</button><button class="btn btn-default js-expense" ${opened ? "" : "disabled"}>Витрата з каси</button><button class="btn btn-default js-cash-in" ${opened ? "" : "disabled"}>Внесення готівки</button><button class="btn btn-default js-cash-report" ${opened ? "" : "disabled"}>Касовий звіт</button></div>` }] });
    dialog.show();
    dialog.$wrapper.on("click", ".js-open", () => { dialog.hide(); openShift(); });
    dialog.$wrapper.on("click", ".js-close", () => { dialog.hide(); closeShift(); });
    dialog.$wrapper.on("click", ".js-incassation", () => { dialog.hide(); cashOperationDialog("Incassation Out", "Інкасація"); });
    dialog.$wrapper.on("click", ".js-expense", () => { dialog.hide(); cashOperationDialog("Expense", "Витрата з каси"); });
    dialog.$wrapper.on("click", ".js-cash-in", () => { dialog.hide(); cashOperationDialog("Cash In", "Внесення готівки"); });
    dialog.$wrapper.on("click", ".js-cash-report", () => { dialog.hide(); showReports(); });
  }

  function fiscalReportHtml(report) {
    const paymentRows = (label, rows) => (rows || []).map((row) => `<tr><td>${esc(label)} · ${esc(row.name || row.code)}</td><td>${money(row.sum)} грн</td></tr>`).join("");
    const salesTaxRows = (report.sales_taxes || []).map((row) => `<tr><td>Податок продажу ${esc(row.letter || row.name || "")} ${money(row.prc)}%</td><td>${money(row.sum)} грн</td></tr>`).join("");
    const returnTaxRows = (report.return_taxes || []).map((row) => `<tr><td>Податок повернення ${esc(row.letter || row.name || "")} ${money(row.prc)}%</td><td>${money(row.sum)} грн</td></tr>`).join("");
    const totals = report.report_type === "OPENING" ? "" : `<div class="fiscal-rule"></div><table><tr><td>Чеків продажу</td><td>${esc(report.sales_receipts_count || 0)}</td></tr><tr class="strong"><td>Продажі</td><td>${money(report.sales_total)} грн</td></tr>${paymentRows("Продаж", report.sales_payforms)}<tr><td>Чеків повернення</td><td>${esc(report.return_receipts_count || 0)}</td></tr><tr class="strong"><td>Повернення</td><td>${money(report.returns_total)} грн</td></tr>${paymentRows("Повернення", report.return_payforms)}<tr class="strong"><td>Чистий оборот</td><td>${money(report.net_total)} грн</td></tr><tr><td>Службове внесення</td><td>${money(report.service_input)} грн</td></tr><tr><td>Службова видача</td><td>${money(report.service_output)} грн</td></tr><tr class="strong"><td>Розрахунковий залишок</td><td>${money(report.cash_balance)} грн</td></tr>${salesTaxRows}${returnTaxRows}</table>`;
    return `<div class="fiscal-form" style="max-width:420px;margin:0 auto;font-family:monospace;color:#111"><div style="text-align:center"><b>${esc(report.organization || "")}</b><br>${esc(report.tax_prefix || "ІД")} ${esc(report.tax_number || report.tax_id || "—")}<br>${esc(report.point_name || "")}<br>${esc(report.point_address || "")}</div><div class="fiscal-rule" style="border-top:1px dashed #555;margin:10px 0"></div><div style="text-align:center;font-size:18px;font-weight:700">${esc(report.title)}</div>${report.non_fiscal ? '<div style="text-align:center;font-weight:700">НЕФІСКАЛЬНИЙ</div>' : ""}${report.testing ? '<div style="text-align:center;font-weight:700">ТЕСТОВИЙ РЕЖИМ</div>' : ""}<p>ФН ПРРО ${esc(report.cash_register_fiscal_number || "—")}<br>Локальний № ПРРО: ${esc(report.cash_desk_local_number || "—")}<br>Фіскальна зміна: ${esc(report.shift)}${report.operational_shift ? `<br>Управлінська зміна: ${esc(report.operational_shift)}` : ""}<br>Касир: ${esc(report.cashier || "—")}<br>Відкрито: ${esc(report.opened_at || "—")}${report.closed_at ? `<br>Закрито: ${esc(report.closed_at)}` : ""}${report.document_at ? `<br>Z-документ: ${esc(report.document_at)}` : ""}</p>${report.report_type === "OPENING" ? '<div style="text-align:center;font-size:17px;font-weight:700">ЗМІНУ ВІДКРИТО</div>' : ""}${totals}${report.fiscal_number ? `<div class="fiscal-rule" style="border-top:1px dashed #555;margin:10px 0"></div><div style="text-align:center"><b>${esc(report.fiscal_number_label || "Фіскальний №")} ${esc(report.fiscal_number)}</b><br>Локальний № документа ${esc(report.local_number)}</div>` : ""}${report.is_offline ? '<div style="text-align:center;font-weight:700">ОФЛАЙН</div>' : ""}<p style="text-align:center;font-size:11px">Надруковано: ${esc(report.generated_at)}</p></div>`;
  }

  function printFiscalHtml(report, html) {
    const win = window.open("", "_blank", "width=520,height=760");
    if (!win) return frappe.msgprint("Браузер заблокував вікно друку.");
    win.document.write(`<!doctype html><html lang="uk"><head><meta charset="utf-8"><title>${esc(report.title)}</title><style>@page{size:80mm auto;margin:4mm}body{width:72mm;margin:0 auto;font:12px/1.35 monospace;color:#000}.fiscal-form{max-width:none!important}table{width:100%;border-collapse:collapse}td{padding:2px 0}td:last-child{text-align:right}.strong{font-weight:700}.fiscal-rule{border-top:1px dashed #000!important;margin:8px 0!important}button{width:100%;margin-top:12px;padding:8px}@media print{button{display:none}}</style></head><body>${html}<button onclick="window.print()">Друкувати</button></body></html>`);
    win.document.close();
    win.focus();
  }

  async function showFiscalReport(reportType, shiftName) {
    const report = await api("fiscal_report_data", { pos_session_token: state.token, report_type: reportType, shift: shiftName });
    const html = fiscalReportHtml(report);
    const dialog = new frappe.ui.Dialog({
      title: report.title,
      fields: [{ fieldname: "report", fieldtype: "HTML", options: html }],
      primary_action_label: "Друкувати",
      primary_action: async () => {
        if (!state.session?.desk?.receipt_printer) return printFiscalHtml(report, html);
        const result = await api("queue_fiscal_report_print", { pos_session_token: state.token, report_type: reportType, shift: shiftName, idem_key: idem() });
        if (result.fallback_browser) return printFiscalHtml(report, html);
        frappe.show_alert({ message: "Звіт поставлено в чергу друку", indicator: "green" });
        dialog.hide();
      },
    });
    dialog.show();
  }

  async function fiscalMenu() {
    const status = await api("fiscal_status", { pos_session_token: state.token });
    const configured = Boolean(status.configured);
    const open = Boolean(status.current_shift);
    const hasLastZ = Boolean(status.last_shift?.z_report_fiscal_number);
    const dialog = new frappe.ui.Dialog({ title: "Фіскальний реєстратор", fields: [{ fieldname: "status", fieldtype: "HTML", options: `<div class="ua-pos-modal-note">ПРРО: <b>${esc(status.register || "не налаштовано")}</b><br>Фіскальна зміна: <b>${open ? esc(status.current_shift.name) : "закрита"}</b></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><button class="btn btn-primary js-fiscal-open" ${configured && !open ? "" : "disabled"}>Відкрити фіскальну зміну</button><button class="btn btn-default js-opening-report" ${configured && open ? "" : "disabled"}>Друк відкриття</button><button class="btn btn-default js-x-report" ${configured && open ? "" : "disabled"}>X-звіт / друк</button><button class="btn btn-danger js-fiscal-close" ${configured && open ? "" : "disabled"}>Z-звіт і закриття</button><button class="btn btn-default js-last-z-report" ${hasLastZ ? "" : "disabled"}>Останній Z-звіт</button><button class="btn btn-default js-fiscal-cash-in" ${openedAttribute()}>Службове внесення</button><button class="btn btn-default js-fiscal-cash-out" ${openedAttribute()}>Службова видача</button></div>${configured ? "" : "<p class='text-muted' style='margin-top:12px'>Прив’яжіть PRRO Cash Register у налаштуваннях каси.</p>"}` }] });
    function openedAttribute() { return configured && open ? "" : "disabled"; }
    dialog.show();
    dialog.$wrapper.on("click", ".js-fiscal-open", async function () {
      const $button = $(this);
      if ($button.prop("disabled")) return;
      $button.prop("disabled", true).text("Відкриваємо зміну…");
      frappe.dom.freeze("Підписуємо документ і очікуємо відповідь ДПС…");
      try {
        const result = await api("fiscal_open_shift", { pos_session_token: state.token });
        dialog.hide();
        await refreshSession();
        frappe.show_alert({ message: "Фіскальну зміну відкрито", indicator: "green" });
        if (result.current_shift?.name) await showFiscalReport("Opening", result.current_shift.name);
      } catch (error) {
        const message = serverErrorMessage(error);
        frappe.msgprint({
          title: "Не вдалося відкрити фіскальну зміну",
          indicator: "red",
          message: esc(message).replaceAll("\n", "<br>"),
        });
      } finally {
        frappe.dom.unfreeze();
        if (dialog.$wrapper.is(":visible")) $button.prop("disabled", false).text("Відкрити фіскальну зміну");
      }
    });
    dialog.$wrapper.on("click", ".js-opening-report", () => { dialog.hide(); showFiscalReport("Opening", status.current_shift.name); });
    dialog.$wrapper.on("click", ".js-x-report", () => { dialog.hide(); showFiscalReport("X", status.current_shift.name); });
    dialog.$wrapper.on("click", ".js-last-z-report", () => { dialog.hide(); showFiscalReport("Z", status.last_shift.name); });
    dialog.$wrapper.on("click", ".js-fiscal-close", () => frappe.confirm("Сформувати Z-звіт і закрити фіскальну зміну?", async () => {
      const shiftName = status.current_shift.name;
      frappe.dom.freeze("Формуємо Z-звіт і закриваємо зміну в ДПС…");
      try {
        await api("fiscal_close_shift", { pos_session_token: state.token });
        dialog.hide();
        await refreshSession();
        frappe.show_alert({ message: "Z-звіт сформовано, фіскальну зміну закрито", indicator: "green" });
        await showFiscalReport("Z", shiftName);
      } catch (error) {
        frappe.msgprint({ title: "Не вдалося закрити фіскальну зміну", indicator: "red", message: esc(serverErrorMessage(error)).replaceAll("\n", "<br>") });
      } finally {
        frappe.dom.unfreeze();
      }
    }));
    dialog.$wrapper.on("click", ".js-fiscal-cash-in", () => { dialog.hide(); cashOperationDialog("Cash In", "Службове внесення"); });
    dialog.$wrapper.on("click", ".js-fiscal-cash-out", () => { dialog.hide(); cashOperationDialog("Incassation Out", "Службова видача"); });
  }

  function stockSearch() {
    const dialog = new frappe.ui.Dialog({
      title: "Пошук товару на складі",
      size: "large",
      fields: [
        { fieldname: "query", fieldtype: "Data", label: "Артикул, назва або штрихкод", reqd: 1 },
        { fieldname: "results", fieldtype: "HTML", options: '<div class="text-muted">Введіть запит і натисніть «Знайти».</div>' },
      ],
      primary_action_label: "Знайти",
      primary_action: async (values) => {
        const rows = await api("stock_search", { pos_session_token: state.token, query: values.query });
        const html = rows.length
          ? `<table class="ua-pos-denoms"><thead><tr><th>Товар</th><th>Штрихкод</th><th>Залишок</th><th>Ціна</th><th></th></tr></thead><tbody>${rows.map((row) => `<tr><td style="text-align:left"><b>${esc(row.item_name)}</b><br><small>${esc(row.item_code)}</small></td><td>${esc(row.barcode || "—")}</td><td>${money(row.actual_qty)} ${esc(row.uom)}</td><td>${money(row.rate)} грн</td><td><button class="btn btn-xs btn-primary js-add-stock" data-item="${esc(row.item_code)}">Додати</button></td></tr>`).join("")}</tbody></table>`
          : '<div class="ua-pos-modal-note">Товарів не знайдено.</div>';
        dialog.fields_dict.results.$wrapper.html(html);
      },
    });
    dialog.show();
    dialog.fields_dict.query.$input.focus();
    dialog.$wrapper.on("click", ".js-add-stock", async function () { await scan(this.dataset.item); dialog.hide(); });
  }

  async function showHeldOrders() {
    const rows = await api("unfinished_orders", { pos_session_token: state.token });
    const dialog = new frappe.ui.Dialog({ title: "Поточні та відкладені чеки", fields: [{ fieldname: "orders", fieldtype: "HTML", options: rows.length ? `<div style="display:grid;gap:8px">${rows.map((row) => `<button class="btn btn-default js-open-order" data-order="${esc(row.name)}" style="display:flex;justify-content:space-between"><span><b>${esc(row.name)}</b> · ${esc(row.customer)}</span><span>${esc(statusLabels[row.status] || row.status)} · ${money(row.grand_total)} грн</span></button>`).join("")}</div>` : '<div class="ua-pos-modal-note">Відкладених чеків немає.</div>' }] });
    dialog.show();
    dialog.$wrapper.on("click", ".js-open-order", async function () {
      const order = await api("get_order", { pos_session_token: state.token, order: this.dataset.order });
      renderOrder(order.status === "Held" ? await api("hold_order", { pos_session_token: state.token, order: order.name }) : order);
      dialog.hide();
    });
  }

  function discountDialog() {
    if (!canEditOrder() || !state.order.items?.length) return;
    const dialog = new frappe.ui.Dialog({
      title: "Знижка на чек",
      fields: [
        { fieldname: "discount_percent", fieldtype: "Percent", label: "Знижка, %" },
        { fieldname: "discount_amount", fieldtype: "Currency", label: "Або фіксована сума, грн" },
        { fieldname: "note", fieldtype: "HTML", options: '<div class="ua-pos-modal-note">Якщо заповнено відсоток, фіксована сума не використовується. Для скасування знижки вкажіть 0.</div>' },
      ],
      primary_action_label: "Застосувати",
      primary_action: async (values) => { renderOrder(await api("set_order_discount", { pos_session_token: state.token, order: state.order.name, discount_percent: values.discount_percent || 0, discount_amount: values.discount_amount || 0 })); dialog.hide(); },
    });
    dialog.show();
  }

  function itemTrackingDialog(rowName) {
    const row = state.order?.items?.find((item) => item.name === rowName);
    if (!row || !canEditOrder()) return;
    const dialog = new frappe.ui.Dialog({
      title: `Партія / серійний номер · ${row.item_code}`,
      fields: [
        { fieldname: "batch_no", fieldtype: "Link", options: "Batch", label: "Партія", default: row.batch_no },
        { fieldname: "serial_no", fieldtype: "Small Text", label: "Серійні номери", default: row.serial_no, description: "По одному номеру в рядку." },
      ],
      primary_action_label: "Зберегти",
      primary_action: async (values) => { renderOrder(await api("set_item_tracking", { pos_session_token: state.token, order: state.order.name, row_name: row.name, batch_no: values.batch_no, serial_no: values.serial_no })); dialog.hide(); },
    });
    dialog.show();
  }

  function mixedPaymentDialog() {
    if (!state.order?.items?.length || state.order.fiscal_mode !== "Fiscal") return;
    const total = flt(state.order.grand_total);
    const dialog = new frappe.ui.Dialog({
      title: "Змішана оплата",
      fields: [
        { fieldname: "due", fieldtype: "HTML", options: `<div class="ua-pos-modal-note">До сплати: <b>${money(total)} грн</b></div>` },
        { fieldname: "cash_amount", fieldtype: "Currency", label: "Готівка", default: total },
        { fieldname: "cash_mode", fieldtype: "Link", options: "Mode of Payment", label: "Спосіб готівкової оплати", default: "Cash", mandatory_depends_on: "eval:doc.cash_amount>0" },
        { fieldname: "card_amount", fieldtype: "Currency", label: "Картка", default: 0 },
        { fieldname: "card_mode", fieldtype: "Link", options: "Mode of Payment", label: "Спосіб карткової оплати", default: "Credit Card", mandatory_depends_on: "eval:doc.card_amount>0" },
      ],
      primary_action_label: "Провести оплату",
      primary_action: async (values) => {
        const cash = flt(values.cash_amount), card = flt(values.card_amount);
        if (Math.abs(cash + card - total) > 0.01) return frappe.msgprint("Сума частин має дорівнювати сумі чека.");
        if (card && !state.session?.desk?.terminal) return frappe.msgprint("Для карткової частини не налаштовано термінал.");
        const payments = [];
        if (cash) payments.push({ mode_of_payment: values.cash_mode, kind: "Cash", amount: cash, tendered_amount: cash, currency: "UAH" });
        if (card) payments.push({ mode_of_payment: values.card_mode, kind: "Card", amount: card, currency: "UAH" });
        const completed = await api("checkout_start", { pos_session_token: state.token, order: state.order.name, payments: JSON.stringify(payments), idem_key: idem() });
        renderOrder(completed); dialog.hide(); if (completed.status === "Payment Unknown") resolveUnknownPayment(completed);
      },
    });
    dialog.show();
  }

  function reportHtml(report) {
    const movementRows = (report.movements || []).map((row) => `<tr><td style="text-align:left">${esc(row.movement_type)}</td><td>${esc(row.direction === "In" ? "Надходження" : "Видача")}</td><td>${money(row.amount)} ${esc(row.currency)}</td><td style="text-align:left">${esc(row.notes || "—")}</td></tr>`).join("");
    const itemRows = (report.item_totals || []).map((row) => `<tr><td style="text-align:left">${esc(row.item_name)}<br><small>${esc(row.item_code)}</small></td><td>${money(row.qty)}</td><td>${money(row.amount)} грн</td></tr>`).join("");
    return `<div class="ua-pos-modal-note"><b>Зміна ${esc(report.shift.name)}</b><br>Продажі: ${money(report.sales_total)} грн · Повернення: ${money(report.returns_total)} грн · Чистий продаж: ${money(report.net_sales)} грн · Готівка в касі: ${money(report.cash_balance)} грн</div><h5>Рух готівки</h5><table class="ua-pos-denoms"><thead><tr><th>Операція</th><th>Напрям</th><th>Сума</th><th>Коментар</th></tr></thead><tbody>${movementRows || '<tr><td colspan="4">Операцій немає</td></tr>'}</tbody></table><h5 style="margin-top:18px">Товарний звіт</h5><table class="ua-pos-denoms"><thead><tr><th>Товар</th><th>Кількість</th><th>Сума</th></tr></thead><tbody>${itemRows || '<tr><td colspan="3">Продажів немає</td></tr>'}</tbody></table>`;
  }

  async function showReports() {
    if (!state.session?.shift) return showNotice("Зміна не відкрита.", "error");
    const report = await api("shift_report", { pos_session_token: state.token });
    const html = reportHtml(report);
    const dialog = new frappe.ui.Dialog({ title: "Касовий і товарний звіт зміни", size: "extra-large", fields: [{ fieldname: "report", fieldtype: "HTML", options: html }], primary_action_label: "Друкувати", primary_action: () => printHtml(`Звіт зміни ${report.shift.name}`, html) });
    dialog.show();
  }

  function printHtml(title, body, targetWindow = null, hideTitle = false) {
    const win = targetWindow || window.open("", "_blank", "width=900,height=700");
    if (!win) return frappe.msgprint("Браузер заблокував вікно друку.");
    win.document.write(`<!doctype html><html lang="uk"><head><meta charset="utf-8"><title>${esc(title)}</title><style>body{font-family:Arial,sans-serif;padding:24px;color:#111}table{width:100%;border-collapse:collapse}th,td{border:1px solid #bbb;padding:6px;text-align:right}th:first-child,td:first-child{text-align:left}.center,.fiscal-center{text-align:center}.total{font-size:24px;font-weight:700}.muted,.fiscal-muted{color:#666;font-size:12px}.fiscal-receipt{max-width:430px;margin:0 auto;font-family:monospace}.fiscal-table td{border-bottom:1px dotted #aaa}.fiscal-qr img{width:190px;height:190px}.fiscal-url{overflow-wrap:anywhere}@media print{button{display:none}}</style></head><body>${hideTitle ? "" : `<h2>${esc(title)}</h2>`}${body}<p><button onclick="window.print()">Друкувати</button></p></body></html>`);
    win.document.close();
    win.focus();
  }

  async function printReceiptBrowser() {
	if (!state.order) return;
    const win = window.open("", "_blank", "width=520,height=760");
    const data = await api("receipt_data", { pos_session_token: state.token, order: state.order.name });
    const order = data.order;
    const fiscal = data.fiscal_receipt;
	let body;
	if (fiscal) {
	  body = fiscal.html;
	} else {
	  const items = (order.items || []).map((row) => `<tr><td>${esc(row.item_name || row.item_code)} × ${esc(row.qty)}</td><td>${money(row.amount)} грн</td></tr>`).join("");
	  const payments = (order.payments_plan || []).filter((row) => row.status === "Confirmed").map((row) => `<tr><td>${esc(row.mode_of_payment)}</td><td>${money(row.amount)} грн</td></tr>`).join("");
	  body = `<div class="center"><b>${esc(data.company.company_name || "")}</b><br>${esc(data.cash_desk)}<br><span class="muted">Касир: ${esc(data.employee_name)}</span></div><p class="center"><b>НЕФІСКАЛЬНИЙ ТОВАРНИЙ ЧЕК</b></p><p><b>ЧЕК ${esc(order.name)}</b></p><table>${items}</table><p class="total">Разом: ${money(order.grand_total)} грн</p><table>${payments}</table>${order.change_amount ? `<p>Решта: ${money(order.change_amount)} грн</p>` : ""}<p class="center muted">Код чека для повернення:<br><b>${esc(order.lookup_token)}</b><br>${esc(data.printed_at)}</p>`;
	}
	printHtml(`${order.order_type === "Return" ? "Повернення" : "Чек"} ${order.name}`, body, win, Boolean(fiscal));
  }

  async function printReceipt() {
    if (!state.order) return;
    if (!state.session?.desk?.receipt_printer) return printReceiptBrowser();
    const result = await api("queue_receipt_print", { pos_session_token: state.token, order: state.order.name, idem_key: idem() });
    if (result.fallback_browser) return printReceiptBrowser();
    frappe.show_alert({ message: result.is_copy ? "Копію чека поставлено в чергу друку" : "Чек поставлено в чергу друку", indicator: "green" });
    renderOrder(await api("get_order", { pos_session_token: state.token, order: state.order.name }));
  }

  function returnPaymentDialog(returnOrder, limits) {
    const available = (limits || []).filter((row) => flt(row.available) > 0);
    const rows = available.map((row) => `<tr><td style="text-align:left">${esc(row.kind)} · ${esc(row.mode_of_payment)}</td><td>${money(row.available)} грн</td><td><input type="number" min="0" max="${esc(row.available)}" step="0.01" value="0" data-kind="${esc(row.kind)}" data-mode="${esc(row.mode_of_payment)}"></td></tr>`).join("");
    const dialog = new frappe.ui.Dialog({
      title: `Виплата повернення · ${returnOrder.name}`,
      fields: [{ fieldname: "plan", fieldtype: "HTML", options: `<div class="ua-pos-modal-note">До повернення покупцю: <b>${money(returnOrder.grand_total)} грн</b></div><table class="ua-pos-denoms"><thead><tr><th>Спосіб</th><th>Доступно</th><th>Повернути</th></tr></thead><tbody>${rows}</tbody></table>` }],
      primary_action_label: "Провести повернення",
      primary_action: async () => {
        const payments = [];
        dialog.$wrapper.find("[data-kind]").each(function () { const amount = flt(this.value); if (amount > 0) payments.push({ kind: this.dataset.kind, mode_of_payment: this.dataset.mode, amount, currency: "UAH" }); });
        if (Math.abs(payments.reduce((sum, row) => sum + row.amount, 0) - flt(returnOrder.grand_total)) > 0.01) return frappe.msgprint("Розподіл виплати має дорівнювати сумі повернення.");
        const completed = await api("checkout_start", { pos_session_token: state.token, order: returnOrder.name, payments: JSON.stringify(payments), idem_key: idem() });
        renderOrder(completed); dialog.hide(); if (completed.status === "Payment Unknown") resolveUnknownPayment(completed); else frappe.show_alert({ message: "Повернення проведено", indicator: "green" });
      },
    });
    dialog.show();
    let remaining = flt(returnOrder.grand_total);
    dialog.$wrapper.find("[data-kind]").each(function () { const max = flt(this.max); const amount = Math.min(max, remaining); this.value = amount; remaining -= amount; });
  }

  function returnItemsDialog(details) {
    const available = (details.items || []).filter((row) => flt(row.available_qty) > 0);
    if (!available.length) return frappe.msgprint("Усі товари з цього чека вже повернено.");
    const rows = available.map((row) => `<tr><td style="text-align:left">${esc(row.item_name)}<br><small>${esc(row.item_code)}</small></td><td>${esc(row.available_qty)} ${esc(row.uom)}</td><td><input type="number" min="0" max="${esc(row.available_qty)}" step="1" value="0" data-return-row="${esc(row.row_name)}"></td><td>${money(row.rate)} грн</td></tr>`).join("");
    const dialog = new frappe.ui.Dialog({
      title: `Повернення за чеком ${details.order.name}`,
      size: "large",
      fields: [{ fieldname: "items", fieldtype: "HTML", options: `<table class="ua-pos-denoms"><thead><tr><th>Товар</th><th>Можна повернути</th><th>Кількість</th><th>Ціна</th></tr></thead><tbody>${rows}</tbody></table>` }],
      primary_action_label: "Створити повернення",
      primary_action: async () => {
        const selected = [];
        dialog.$wrapper.find("[data-return-row]").each(function () { const qty = flt(this.value); if (qty > 0) selected.push({ row_name: this.dataset.returnRow, qty }); });
        const returnOrder = await api("create_return_order", { pos_session_token: state.token, token: details.order.lookup_token, items: JSON.stringify(selected), idem_key: idem() });
        dialog.hide(); returnPaymentDialog(returnOrder, details.refund_limits);
      },
    });
    dialog.show();
  }

  function startReturn() {
    const dialog = new frappe.ui.Dialog({
      title: "Повернення товару",
      fields: [{ fieldname: "token", fieldtype: "Data", label: "Код первинного чека", reqd: 1, description: "Відскануйте або введіть код, надрукований унизу чека." }],
      primary_action_label: "Знайти чек",
      primary_action: async (values) => { const details = await api("return_details", { pos_session_token: state.token, token: values.token }); dialog.hide(); returnItemsDialog(details); },
    });
    dialog.show();
    dialog.fields_dict.token.$input.focus();
  }

  async function maybeOfferBirthday() {
    if (!canEditOrder() || !state.order?.customer || state.order.order_type === "Return") return;
    let offer;
    try {
      offer = await api("birthday_offer", { pos_session_token: state.token, customer: state.order.customer, order: state.order.name });
    } catch (error) {
      return;
    }
    if (!offer?.eligible || offer.applied || !flt(offer.discount_percent)) return;
    const promptKey = `${state.order.name}:${state.order.customer}:${offer.benefit_year}`;
    if (state.birthdayPromptKey === promptKey) return;
    state.birthdayPromptKey = promptKey;
    const customerName = offer.customer?.ua_first_name || offer.customer?.customer_name || state.order.customer;
    frappe.confirm(
      `${esc(customerName)}, доступна знижка до дня народження <b>${money(offer.discount_percent)}%</b> до ${esc(frappe.datetime.str_to_user(offer.valid_until))}.<br><br>Застосувати її до поточного чека?`,
      async () => {
        renderOrder(await api("apply_birthday_discount", { pos_session_token: state.token, order: state.order.name }));
        frappe.show_alert({ message: "Знижку до дня народження застосовано", indicator: "green" });
      },
    );
  }

  function promptNewCustomer(result) {
    return new Promise((resolve) => {
      let completed = false;
      const finish = (value) => {
        if (completed) return;
        completed = true;
        resolve(value);
      };
      const dialog = new frappe.ui.Dialog({
        title: "Новий покупець",
        size: "large",
        fields: [
          { fieldname: "phone", fieldtype: "Data", label: "Телефон", default: result.phone, read_only: 1 },
          { fieldname: "identity_section", fieldtype: "Section Break", label: "Основні дані" },
          { fieldname: "last_name", fieldtype: "Data", label: "Прізвище", reqd: 1 },
          { fieldname: "first_name", fieldtype: "Data", label: "Ім’я", reqd: 1 },
          { fieldname: "middle_name", fieldtype: "Data", label: "По батькові" },
          { fieldname: "identity_column", fieldtype: "Column Break" },
          { fieldname: "gender", fieldtype: "Link", options: "Gender", label: "Стать" },
          { fieldname: "date_of_birth", fieldtype: "Date", label: "Дата народження" },
          { fieldname: "contact_section", fieldtype: "Section Break", label: "Контактні дані" },
          { fieldname: "email", fieldtype: "Data", options: "Email", label: "Email" },
          { fieldname: "city", fieldtype: "Data", label: "Місто" },
          { fieldname: "comment", fieldtype: "Small Text", label: "Коментар" },
          { fieldname: "privacy_note", fieldtype: "HTML", options: '<div class="ua-pos-modal-note">Телефон уже підтверджений покупцем. Картка створиться без переходу зі сторінки каси. Якщо покупець відмовився від реєстрації, натисніть «Без створення».</div>' },
        ],
        primary_action_label: "Створити й вибрати",
        primary_action: async (values) => {
          dialog.get_primary_btn().prop("disabled", true);
          try {
            const customer = await identificationApi("quick_create", { request_id: result.request_id, ...values });
            finish(customer);
            dialog.hide();
          } finally {
            dialog.get_primary_btn().prop("disabled", false);
          }
        },
      });
      dialog.set_secondary_action_label("Без створення");
      dialog.set_secondary_action(() => {
        finish(null);
        dialog.hide();
      });
      dialog.$wrapper.on("hidden.bs.modal", () => finish(null));
      dialog.show();
      dialog.fields_dict.last_name.$input.focus();
    });
  }

  async function useIdentifiedCustomer(result) {
    if (result.status !== "Verified") return false;
    let customer = result.customer;
    if (!customer) {
      customer = await promptNewCustomer(result);
      if (!customer) {
        frappe.show_alert({ message: "Продаж продовжено на роздрібного покупця", indicator: "blue" });
        return true;
      }
    }
    renderOrder(
      await api("set_order_customer", {
        pos_session_token: state.token,
        order: state.order.name,
        customer: customer.name,
      }),
    );
    frappe.show_alert({ message: `Покупця ${customer.customer_name || customer.name} ідентифіковано`, indicator: "green" });
    await maybeOfferBirthday();
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

  function maskUkrainianPhone(value) {
    let digits = String(value || "").replace(/\D/g, "");
    if (digits.startsWith("380")) digits = digits.slice(2);
    else if (digits.startsWith("38")) digits = digits.slice(2);
    else if (!digits.startsWith("0")) digits = `0${digits}`;
    digits = digits.slice(0, 10);
    if (!digits) return "";
    let masked = "+38";
    masked += ` (${digits.slice(0, 3)}`;
    if (digits.length >= 3) masked += ")";
    if (digits.length > 3) masked += ` ${digits.slice(3, 6)}`;
    if (digits.length > 6) masked += `-${digits.slice(6, 8)}`;
    if (digits.length > 8) masked += `-${digits.slice(8, 10)}`;
    return masked;
  }

  async function useRetailCustomer() {
    const retailCustomer = state.session?.desk?.default_customer;
    if (canEditOrder() && retailCustomer && state.order.customer !== retailCustomer) {
      renderOrder(await api("set_order_customer", { pos_session_token: state.token, order: state.order.name, customer: retailCustomer }));
    }
    frappe.show_alert({ message: "Продаж продовжено на роздрібного покупця", indicator: "blue" });
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
        { fieldname: "phone", fieldtype: "Data", label: "Номер телефону", reqd: 1, placeholder: "+38 (0XX) XXX-XX-XX" },
        { fieldname: "channel", fieldtype: "Select", label: "Канал підтвердження", options: config.channels.join("\n"), reqd: 1, default: config.channels[0] },
        { fieldname: "note", fieldtype: "HTML", options: '<div class="ua-pos-modal-note">Покупець підтверджує, що має доступ до вказаного номера. Код і технічні дані не зберігаються у відкритому вигляді.</div>' },
      ],
      primary_action_label: "Надіслати запит",
      primary_action: async (values) => {
        dialog.get_primary_btn().prop("disabled", true);
        try {
          const lookup = await identificationApi("find_by_phone", { phone: values.phone });
          const startVerification = async () => {
            const request = await identificationApi("begin", {
              channel: values.channel,
              phone: lookup.phone,
              reference_doctype: "POS Order",
              reference_name: state.order.name,
            });
            verificationDialog(request);
          };
          dialog.hide();
          if (lookup.customer) {
            await startVerification();
          } else {
            frappe.confirm(
              `Клієнта з номером <b>${esc(lookup.phone)}</b> не знайдено.<br><br>Створити нового клієнта?`,
              startVerification,
              useRetailCustomer,
            );
          }
        } finally {
          dialog.get_primary_btn().prop("disabled", false);
        }
      },
    });
    dialog.show();
    dialog.fields_dict.phone.$input
      .attr({ inputmode: "tel", maxlength: 19 })
      .on("input.ua_pos_phone", function () { this.value = maskUkrainianPhone(this.value); })
      .focus();
  }

  async function selectCustomer() {
    if (!state.session?.shift) return showNotice("Спочатку відкрийте управлінську зміну.", "error");
    if (!state.order || !canEditOrder()) await newOrder();
    frappe.prompt(
      { fieldname: "customer", fieldtype: "Link", options: "Customer", label: "Клієнт", reqd: 1, default: state.order.customer },
      async (values) => {
        renderOrder(await api("set_order_customer", { pos_session_token: state.token, order: state.order.name, customer: values.customer }));
        state.birthdayPromptKey = null;
        await maybeOfferBirthday();
      },
      "Вибір клієнта",
      "Застосувати",
    );
  }

  async function createInvoice() {
    if (!canEditOrder() || !state.order.items?.length) return showNotice("Додайте товари до рахунку.", "error");
    if (!state.order.customer || state.order.customer === state.session?.desk?.default_customer) {
      return showNotice("Для рахунку спочатку виберіть або ідентифікуйте покупця.", "error");
    }
    const invoice = await api("create_draft_invoice", { pos_session_token: state.token, order: state.order.name });
    renderOrder(null);
    renderSession();
    frappe.msgprint({
      title: "Рахунок створено",
      indicator: "green",
      message: `Створено чернетку <b>${esc(invoice.name)}</b> на суму <b>${money(invoice.grand_total)} грн</b>.<br>Товар не видано і склад не списано.<br><br><a class="btn btn-primary btn-sm" target="_blank" href="/app/sales-invoice/${encodeURIComponent(invoice.name)}">Відкрити рахунок</a>`,
    });
  }

  $root.on("click", ".ua-pos-login-button", login);
  $root.on("keydown", ".ua-pos-login-barcode", (event) => { if (event.key === "Enter") login(); });
  $root.on("click", ".js-logout", async () => { await api("logout", { pos_session_token: state.token }); sessionStorage.removeItem("ua_pos_token"); location.reload(); });
  $root.on("keydown", ".ua-pos-scan", async (event) => { if (event.key !== "Enter") return; const query = event.currentTarget.value.trim(); if (!query) return; event.currentTarget.value = ""; await scan(query); });
  $root.on("click", ".js-new-order", newOrder);
  $root.on("click", ".js-layout", layoutDialog);
  $root.on("click", ".js-service-info", serviceInfoDialog);
  $root.on("click", ".ua-pos-mode button", async function () { const mode = this.dataset.mode; if (mode === "Fiscal" && !state.session?.desk?.prro_cash_register) return showNotice("Фіскальний режим недоступний: для каси не налаштовано ПРРО.", "error"); state.saleMode = mode; if (canEditOrder()) renderOrder(await api("set_order_mode", { pos_session_token: state.token, order: state.order.name, fiscal_mode: mode })); else renderSession(); });
  $root.on("click", ".ua-pos-qty button", async function () { const rowName = $(this).closest("tr").data("row"); const row = state.order.items.find((item) => item.name === rowName); renderOrder(await api("set_item_qty", { pos_session_token: state.token, order: state.order.name, row_name: rowName, qty: flt(row.qty) + flt(this.dataset.delta) })); });
  $root.on("click", ".js-track-item", function () { itemTrackingDialog($(this).closest("tr").data("row")); });
  $root.on("click", ".js-customer", selectCustomer);
  $root.on("click", ".js-identify", identifyCustomer);
  $root.on("click", ".js-create-invoice", createInvoice);
  $root.on("click", ".js-discount", discountDialog);
  $root.on("click", ".js-hold", async () => { if (state.order) renderOrder(await api("hold_order", { pos_session_token: state.token, order: state.order.name })); });
  $root.on("click", ".js-orders", showHeldOrders);
  $root.on("click", ".js-cancel", async () => {
    if (!state.order || !canEditOrder()) return;
    const orderName = state.order.name;
    frappe.confirm("Скасувати поточний неоплачений чек?", async () => {
      await api("cancel_order", { pos_session_token: state.token, order: orderName });
      renderOrder(null);
      renderSession();
      $root.find(".ua-pos-scan").val("").focus();
      frappe.show_alert({ message: "Чек скасовано. Каса готова до нового продажу", indicator: "blue" });
    });
  });
  $root.on("click", ".js-pay-cash", () => paymentDialog("Cash"));
  $root.on("click", ".js-pay-card", () => paymentDialog("Card"));
  $root.on("click", ".js-pay-mixed", mixedPaymentDialog);
  $root.on("click", ".js-print", printReceipt);
  $root.on("click", ".js-cash-menu", cashMenu);
  $root.on("click", ".js-fiscal-menu", fiscalMenu);
  $root.on("click", ".js-retry-fiscal", async () => {
    if (!state.order) return;
    const button = $root.find(".js-retry-fiscal");
    button.prop("disabled", true).text("Звіряємо з ДПС…");
    try {
      const recovered = await api("retry_fiscalization", { pos_session_token: state.token, order: state.order.name });
      renderOrder(recovered);
      if (["Completed", "Printing", "Completed Print Error"].includes(recovered.status)) {
        clearNotice();
        frappe.show_alert({ message: `${recovered.name}: фіскалізацію підтверджено`, indicator: "green" });
      }
    } finally {
      button.prop("disabled", false).text("↻ Відновити фіскалізацію");
    }
  });
  $root.on("click", ".js-stock", stockSearch);
  $root.on("click", ".js-reports", showReports);
  $root.on("click", ".js-return", startReturn);

  $(document).off("keydown.ua_pos").on("keydown.ua_pos", (event) => {
    if ($(event.target).is("input,textarea,select") && !/^F\d{1,2}$/.test(event.key)) return;
    const actions = { F2: () => $root.find(".ua-pos-scan").focus(), F3: () => $root.find(".js-stock").click(), F4: selectCustomer, F5: identifyCustomer, F6: discountDialog, F7: () => $root.find(".js-hold").click(), F8: startReturn, F9: () => { if (!$root.find(".js-pay-cash").first().prop("disabled")) paymentDialog("Cash"); } };
    if (actions[event.key]) { event.preventDefault(); actions[event.key](); }
    if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "s") { event.preventDefault(); openShift(); }
    if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "c") { event.preventDefault(); closeShift(); }
  });

  updateClock(); state.clock = setInterval(updateClock, 1000); loadCashDesks(); refreshSession();
};

frappe.pages["ua-pos"].on_page_hide = function (wrapper) {
  $(document).off("keydown.ua_pos");
  if (wrapper.uaPosState?.clock) clearInterval(wrapper.uaPosState.clock);
};
