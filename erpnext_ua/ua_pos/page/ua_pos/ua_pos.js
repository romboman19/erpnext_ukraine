frappe.pages["ua-pos"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: __("UA POS"), single_column: true });
  const state = { token: sessionStorage.getItem("ua_pos_token"), order: null, session: null };
  const $root = $(
    `<div class="ua-pos-shell">
      <div class="ua-pos-status"><span class="status">${__("Not signed in")}</span><span class="shift"></span></div>
      <div class="ua-pos-login">
        <input class="form-control desk" placeholder="${__("Cash desk")}">
        <input class="form-control barcode" type="password" placeholder="${__("Employee barcode")}">
        <button class="btn btn-primary login">${__("Sign in")}</button>
      </div>
      <div class="ua-pos-work" style="display:none">
        <div class="ua-pos-toolbar">
          <button class="btn btn-default open-shift">${__("Open shift")}</button>
          <button class="btn btn-default close-shift">${__("Close shift")}</button>
          <button class="btn btn-default new-order">${__("New sale")}</button>
          <button class="btn btn-light logout">${__("Sign out")}</button>
        </div>
        <input class="form-control scan" placeholder="${__("Scan barcode or enter item code")}" autocomplete="off">
        <table class="table table-bordered cart"><thead><tr><th>${__("Item")}</th><th>${__("Qty")}</th><th>${__("Rate")}</th><th>${__("Amount")}</th></tr></thead><tbody></tbody></table>
        <div class="ua-pos-total"><strong>${__("Total")}: <span>0.00</span> UAH</strong></div>
        <div class="ua-pos-pay"><button class="btn btn-success cash">F9 · ${__("Cash")}</button><button class="btn btn-primary card">${__("Card")}</button></div>
      </div>
    </div>`
  ).appendTo(page.body);

  $(`<style>
    .ua-pos-shell{max-width:1200px;margin:auto;font-size:16px}.ua-pos-status,.ua-pos-toolbar,.ua-pos-login,.ua-pos-pay{display:flex;gap:10px;align-items:center;margin:12px 0}.ua-pos-status{justify-content:space-between;padding:12px;background:var(--fg-color);border-radius:8px}.ua-pos-login input{max-width:280px}.ua-pos-work .scan{font-size:22px;height:52px;margin:14px 0}.ua-pos-total{text-align:right;font-size:28px;margin:16px}.ua-pos-pay{justify-content:flex-end}.ua-pos-pay button{min-width:150px;height:52px}.cart td:nth-child(n+2){text-align:right}
  </style>`).appendTo(page.body);

  const api = (method, args = {}) => frappe.call({ method: `erpnext_ua.ua_pos.api.${method}`, args }).then(r => r.message);
  const idem = () => crypto.randomUUID();

  function render(order) {
    state.order = order;
    const rows = (order?.items || []).map(r => `<tr><td>${frappe.utils.escape_html(r.item_name || r.item_code)}</td><td>${r.qty}</td><td>${format_currency(r.rate, "UAH")}</td><td>${format_currency(r.amount, "UAH")}</td></tr>`).join("");
    $root.find("tbody").html(rows);
    $root.find(".ua-pos-total span").text(format_number(order?.grand_total || 0, null, 2));
    $root.find(".scan").focus();
  }

  async function refreshSession() {
    if (!state.token) return;
    try {
      state.session = await api("session_state", { pos_session_token: state.token });
      $root.find(".ua-pos-login").hide(); $root.find(".ua-pos-work").show();
      $root.find(".status").text(`${state.session.employee} · ${state.session.cash_desk}`);
      $root.find(".shift").text(state.session.shift ? `${__("Shift")}: ${state.session.shift}` : __("Shift closed"));
    } catch (_) { sessionStorage.removeItem("ua_pos_token"); state.token = null; }
  }

  $root.on("click", ".login", async () => {
    const $barcode = $root.find(".barcode");
    try {
      const result = await api("login_by_barcode", {
        cash_desk: ($root.find(".desk").val() || "").trim(),
        barcode: ($barcode.val() || "").trim(),
        device_token: navigator.userAgent,
      });
      state.token = result.session_token;
      sessionStorage.setItem("ua_pos_token", state.token);
      await refreshSession();
    } finally {
      $barcode.val("").focus();
    }
  });
  $root.on("click", ".logout", async () => { await api("logout", { pos_session_token: state.token }); sessionStorage.removeItem("ua_pos_token"); location.reload(); });
  $root.on("click", ".open-shift", () => frappe.prompt({fieldname:"amount",fieldtype:"Currency",label:__("Opening cash"),reqd:1}, async v => { await api("open_shift", {pos_session_token:state.token,denominations:JSON.stringify([{currency:"UAH",denomination:v.amount,qty:1}]),idem_key:idem()}); await refreshSession(); }));
  $root.on("click", ".close-shift", async () => { const summary=await api("close_shift_begin",{pos_session_token:state.token}); frappe.prompt([{fieldname:"amount",fieldtype:"Currency",label:`${__("Counted cash")} (${summary.expected})`,reqd:1},{fieldname:"comment",fieldtype:"Small Text",label:__("Comment")}], async v => { await api("close_shift_confirm",{pos_session_token:state.token,denominations:JSON.stringify([{currency:"UAH",denomination:v.amount,qty:1}]),comment:v.comment||"",idem_key:idem()}); await refreshSession(); render(null); }); });
  $root.on("click", ".new-order", async () => render(await api("create_order", {pos_session_token:state.token,idem_key:idem()})));
  $root.on("keydown", ".scan", async e => { if(e.key!=="Enter"||!e.target.value.trim()||!state.order)return; const q=e.target.value.trim();e.target.value="";render(await api("scan_item",{pos_session_token:state.token,order:state.order.name,query:q})); });
  async function pay(kind) { if(!state.order)return; const mop=await new Promise(resolve=>frappe.prompt({fieldname:"mode",fieldtype:"Link",options:"Mode of Payment",label:__("Mode of Payment"),reqd:1},v=>resolve(v.mode))); const out=await api("checkout_start",{pos_session_token:state.token,order:state.order.name,payments:JSON.stringify([{mode_of_payment:mop,kind,amount:state.order.grand_total,currency:"UAH"}]),idem_key:idem()});render(out);frappe.show_alert({message:`${out.name}: ${out.status}`,indicator:out.status==="Completed"?"green":"orange"}); }
  $root.on("click", ".cash",()=>pay("Cash")); $root.on("click", ".card",()=>pay("Card"));
  $(document).on("keydown.ua_pos", e => { if(e.key==="F9"){e.preventDefault();$root.find(".cash").click();} });
  refreshSession();
};

frappe.pages["ua-pos"].on_page_hide = function () { $(document).off("keydown.ua_pos"); };
