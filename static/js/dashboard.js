/* ==========================================================================
   dashboard.js — Lógica de la aplicación principal (usuario).
   ========================================================================== */

let currentStocks = [];      // últimos datos del mercado recibidos del servidor
let currentUser = null;      // {id, name}
let miniCharts = {};         // instancias Chart.js de las tarjetas de mercado
let tradeChart = null;       // instancia Chart.js del modal de operación
let activeTradeStock = null; // acción seleccionada en el modal
let tradeMode = "buy";       // 'buy' | 'sell'
let refreshTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initSoundToggleButton();
  bindLoginEvents();
  bindAppEvents();
  tryAutoLogin();
});

/* --------------------------------------------------------------------
   Autenticación
   -------------------------------------------------------------------- */
function bindLoginEvents() {
  const loginBtn = document.getElementById("loginBtn");
  const nameInput = document.getElementById("nameInput");
  const passwordInput = document.getElementById("passwordInput");

  loginBtn.addEventListener("click", doLogin);
  nameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") passwordInput.focus();
  });
  passwordInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doLogin();
  });
}

async function tryAutoLogin() {
  // Si ya existe una sesión de servidor activa, entra directo al dashboard.
  const res = await apiFetch("/api/me");
  if (res.ok) {
    enterApp(res.user);
  }
}

async function doLogin() {
  const nameInput = document.getElementById("nameInput");
  const passwordInput = document.getElementById("passwordInput");
  const errorBox = document.getElementById("loginError");
  const name = nameInput.value.trim();
  const password = passwordInput.value;

  errorBox.classList.remove("show");

  if (!name) {
    errorBox.textContent = "Escribe tu nombre de usuario.";
    errorBox.classList.add("show");
    return;
  }
  if (!password || password.length < 4) {
    errorBox.textContent = "Escribe una contraseña de al menos 4 caracteres.";
    errorBox.classList.add("show");
    return;
  }

  const loginBtn = document.getElementById("loginBtn");
  loginBtn.disabled = true;
  loginBtn.textContent = "Entrando...";

  const res = await apiFetch("/api/login", { method: "POST", body: { name, password } });

  loginBtn.disabled = false;
  loginBtn.textContent = "Entrar a la bolsa";

  if (!res.ok) {
    errorBox.textContent = res.error || "No se pudo iniciar sesión.";
    errorBox.classList.add("show");
    return;
  }

  enterApp(res.user);
}

function enterApp(user) {
  currentUser = user;
  document.getElementById("loginScreen").classList.add("hidden");
  document.getElementById("appShell").classList.remove("hidden");
  document.getElementById("userNameLabel").textContent = user.name;

  refreshEverything();
  refreshTimer = setInterval(refreshEverything, 4000);
}

async function doLogout() {
  await apiFetch("/api/logout", { method: "POST" });
  clearInterval(refreshTimer);
  window.location.reload();
}

/* --------------------------------------------------------------------
   Eventos generales de la aplicación
   -------------------------------------------------------------------- */
function bindAppEvents() {
  document.getElementById("logoutBtn").addEventListener("click", doLogout);
  document.getElementById("themeToggleBtn").addEventListener("click", toggleTheme);

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view, btn));
  });

  document.getElementById("stockSearchInput").addEventListener(
    "input",
    debounce(() => renderMarketGrid(), 200)
  );
  document.getElementById("stockFilterSelect").addEventListener("change", renderMarketGrid);

  document.getElementById("closeTradeModalBtn").addEventListener("click", closeTradeModal);
  document.getElementById("tradeModalOverlay").addEventListener("click", (e) => {
    if (e.target.id === "tradeModalOverlay") closeTradeModal();
  });

  bindMoneyRequestEvents();

  document.getElementById("buyTabBtn").addEventListener("click", () => setTradeMode("buy"));
  document.getElementById("sellTabBtn").addEventListener("click", () => setTradeMode("sell"));
  document.getElementById("tradeQuantityInput").addEventListener("input", updateTradeSummary);
  document.getElementById("confirmTradeBtn").addEventListener("click", confirmTrade);
}

function switchView(viewId, btnEl) {
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.getElementById(viewId).classList.add("active");
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
  btnEl.classList.add("active");

  if (viewId === "portfolioView") loadPortfolio();
  if (viewId === "historyView") loadHistory();
  if (viewId === "rankingView") loadRanking();
}

/* --------------------------------------------------------------------
   Carga periódica de datos
   -------------------------------------------------------------------- */
async function refreshEverything() {
  await loadStocks();
  await loadMeSummary();

  const activeView = document.querySelector(".view.active");
  if (activeView) {
    if (activeView.id === "portfolioView") loadPortfolio();
    if (activeView.id === "rankingView") loadRanking();
  }

  if (document.getElementById("tradeModalOverlay").classList.contains("show") && activeTradeStock) {
    const fresh = currentStocks.find((s) => s.id === activeTradeStock.id);
    if (fresh) {
      activeTradeStock = fresh;
      updateTradeSummary();
    }
  }
}

async function loadStocks() {
  const res = await apiFetch("/api/stocks");
  if (!res.ok) return;
  currentStocks = res.stocks;
  renderTicker();
  renderMarketGrid();
  renderQuickMarket();
}

async function loadMeSummary() {
  const res = await apiFetch("/api/me");
  if (!res.ok) return;
  document.getElementById("userBalanceLabel").textContent = formatMoney(res.summary.available_cash);
  renderDashboardStats(res.summary);
  loadUserStats();
  loadMoneyRequests();
}

/* --------------------------------------------------------------------
   Solicitudes de dinero ficticio: depósito y retiro (requieren aprobación admin)
   -------------------------------------------------------------------- */
let moneyRequestMode = "deposit"; // 'deposit' | 'withdraw'

function bindMoneyRequestEvents() {
  document.getElementById("depositTabBtn").addEventListener("click", () => setMoneyRequestMode("deposit"));
  document.getElementById("withdrawTabBtn").addEventListener("click", () => setMoneyRequestMode("withdraw"));
  document.getElementById("requestDepositBtn").addEventListener("click", requestMoney);
}

function setMoneyRequestMode(mode) {
  moneyRequestMode = mode;
  const depositTab = document.getElementById("depositTabBtn");
  const withdrawTab = document.getElementById("withdrawTabBtn");
  const btn = document.getElementById("requestDepositBtn");

  depositTab.classList.toggle("active", mode === "deposit");
  withdrawTab.classList.toggle("active", mode === "withdraw");

  if (mode === "deposit") {
    btn.className = "btn btn-buy";
    btn.style.marginTop = "0";
    btn.style.width = "auto";
    btn.textContent = "Enviar solicitud";
  } else {
    btn.className = "btn btn-sell";
    btn.style.marginTop = "0";
    btn.style.width = "auto";
    btn.textContent = "Enviar solicitud";
  }
}

async function requestMoney() {
  const amountInput = document.getElementById("depositAmountInput");
  const noteInput = document.getElementById("depositNoteInput");
  const amount = parseFloat(amountInput.value);

  if (!amount || amount <= 0) {
    showToast("Ingresa un monto válido para solicitar.", "error");
    return;
  }

  const endpoint = moneyRequestMode === "deposit" ? "/api/money/deposit-request" : "/api/money/withdraw-request";
  const btn = document.getElementById("requestDepositBtn");
  btn.disabled = true;

  const res = await apiFetch(endpoint, {
    method: "POST",
    body: { amount, note: noteInput.value.trim() },
  });

  btn.disabled = false;

  if (!res.ok) {
    showToast(res.error || "No se pudo enviar la solicitud.", "error");
    return;
  }

  showToast(res.message, "success");
  amountInput.value = "";
  noteInput.value = "";
  loadMoneyRequests();
}

async function loadMoneyRequests() {
  const res = await apiFetch("/api/money/history");
  if (!res.ok) return;
  const wrap = document.getElementById("moneyRequestsWrap");

  if (res.requests.length === 0) {
    wrap.innerHTML = `<p class="text-dim" style="font-size:13px;">Aún no has hecho ninguna solicitud de dinero.</p>`;
    return;
  }

  const statusLabel = { pending: "⏳ Pendiente", approved: "✅ Aprobado", rejected: "❌ Rechazado" };
  const statusClass = { pending: "", approved: "badge-buy", rejected: "badge-sell" };
  const typeLabel = { deposit: "Depósito", withdraw: "Retiro" };

  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Fecha</th><th>Tipo</th><th>Monto</th><th>Motivo</th><th>Estado</th></tr></thead>
      <tbody>
        ${res.requests.map((r) => `
          <tr>
            <td>${r.requested_at}</td>
            <td>${typeLabel[r.type] || r.type}</td>
            <td>${formatMoney(r.amount)}</td>
            <td>${r.note || "—"}</td>
            <td>${statusClass[r.status] ? `<span class="${statusClass[r.status]}">${statusLabel[r.status]}</span>` : statusLabel[r.status]}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

async function loadUserStats() {
  const res = await apiFetch("/api/stats");
  if (!res.ok) return;
  const grid = document.getElementById("userStatsGrid");
  const best = res.best_trade ? `${res.best_trade.name} (${formatMoney(res.best_trade.profit_loss)})` : "—";
  const worst = res.worst_trade ? `${res.worst_trade.name} (${formatMoney(res.worst_trade.profit_loss)})` : "—";
  grid.innerHTML = `
    <div class="stat-card"><div class="label">Operaciones totales</div><div class="value">${res.total_trades}</div></div>
    <div class="stat-card"><div class="label">Compras</div><div class="value">${res.buys}</div></div>
    <div class="stat-card"><div class="label">Ventas</div><div class="value">${res.sells}</div></div>
    <div class="stat-card"><div class="label">Mejor operación</div><div class="value" style="font-size:15px;">${best}</div></div>
  `;
}

/* --------------------------------------------------------------------
   Ticker superior (elemento distintivo, cinta en movimiento)
   -------------------------------------------------------------------- */
function renderTicker() {
  const track = document.getElementById("tickerTrack");
  const items = currentStocks.concat(currentStocks); // duplicar para loop continuo
  track.innerHTML = items
    .map((s) => {
      const cls = s.change_percent > 0 ? "up" : s.change_percent < 0 ? "down" : "";
      const arrow = s.change_percent > 0 ? "▲" : s.change_percent < 0 ? "▼" : "•";
      return `<span class="ticker-item"><b>${s.symbol}</b> ${formatMoney(s.price)} <span class="${cls}">${arrow} ${formatPct(s.change_percent)}</span></span>`;
    })
    .join("");
}

/* --------------------------------------------------------------------
   Tarjetas de resumen del dashboard
   -------------------------------------------------------------------- */
function renderDashboardStats(summary) {
  const grid = document.getElementById("dashboardStats");
  const profitClass = summary.unrealized_profit >= 0 ? "positive" : "negative";
  const yieldClass = summary.yield_pct >= 0 ? "positive" : "negative";

  grid.innerHTML = `
    <div class="stat-card">
      <div class="label">Dinero disponible</div>
      <div class="value">${formatMoney(summary.available_cash)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Dinero invertido</div>
      <div class="value">${formatMoney(summary.invested)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Ganancia / pérdida no realizada</div>
      <div class="value ${profitClass}">${formatMoney(summary.unrealized_profit)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Ganancia realizada</div>
      <div class="value ${summary.realized_profit >= 0 ? 'positive' : 'negative'}">${formatMoney(summary.realized_profit)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Patrimonio total</div>
      <div class="value">${formatMoney(summary.equity)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Rentabilidad</div>
      <div class="value ${yieldClass}">${formatPct(summary.yield_pct)}</div>
    </div>
  `;
}

/* --------------------------------------------------------------------
   Mercado — tarjetas con mini gráfico
   -------------------------------------------------------------------- */
function getFilteredStocks() {
  const query = document.getElementById("stockSearchInput").value.trim().toLowerCase();
  const filter = document.getElementById("stockFilterSelect").value;

  return currentStocks.filter((s) => {
    const matchesQuery = !query || s.name.toLowerCase().includes(query) || s.symbol.toLowerCase().includes(query);
    if (!matchesQuery) return false;
    if (filter === "up") return s.change_percent > 0;
    if (filter === "down") return s.change_percent < 0;
    return true;
  });
}

async function renderMarketGrid() {
  const grid = document.getElementById("marketGrid");
  let stocks = getFilteredStocks();
  const filter = document.getElementById("stockFilterSelect").value;

  if (filter === "owned") {
    const portfolioRes = await apiFetch("/api/portfolio");
    const ownedIds = portfolioRes.ok ? portfolioRes.summary.positions.map((p) => p.stock_id) : [];
    stocks = stocks.filter((s) => ownedIds.includes(s.id));
  }

  if (stocks.length === 0) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;"><div class="big-icon">📭</div>No se encontraron acciones con ese criterio.</div>`;
    return;
  }

  grid.innerHTML = stocks.map(stockCardHtml).join("");

  stocks.forEach((s) => {
    grid.querySelector(`[data-stock-card="${s.id}"]`).addEventListener("click", () => openTradeModal(s.id));
  });

  stocks.forEach((s) => renderMiniChart(s.id));
}

function stockCardHtml(s) {
  const cls = s.change_percent > 0 ? "up" : s.change_percent < 0 ? "down" : "flat";
  return `
    <div class="stock-card" data-stock-card="${s.id}">
      <div class="stock-card-head">
        <div>
          <div class="stock-symbol">${s.symbol}</div>
          <div class="stock-name">${s.name}</div>
        </div>
        <div class="stock-change ${cls}">${formatPct(s.change_percent)}</div>
      </div>
      <div class="stock-price">${formatMoney(s.price)}</div>
      <div class="mini-chart"><canvas id="miniChart-${s.id}"></canvas></div>
      <div class="stock-actions">
        <button class="btn btn-buy btn-sm">Comprar</button>
        <button class="btn btn-sell btn-sm">Vender</button>
      </div>
    </div>
  `;
}

async function renderMiniChart(stockId) {
  const canvas = document.getElementById(`miniChart-${stockId}`);
  if (!canvas) return;
  const res = await apiFetch(`/api/stocks/${stockId}/history?limit=30`);
  if (!res.ok) return;

  const prices = res.history.map((h) => h.price);
  const isUp = prices.length > 1 ? prices[prices.length - 1] >= prices[0] : true;
  const color = isUp ? "#1fd67a" : "#ff4d5e";

  if (miniCharts[stockId]) miniCharts[stockId].destroy();
  miniCharts[stockId] = new Chart(canvas, {
    type: "line",
    data: {
      labels: prices.map((_, i) => i),
      datasets: [{
        data: prices,
        borderColor: color,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.35,
        fill: false,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: { x: { display: false }, y: { display: false } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    },
  });
}

function renderQuickMarket() {
  const grid = document.getElementById("quickMarketGrid");
  const top = currentStocks.slice(0, 4);
  grid.innerHTML = top.map((s) => {
    const cls = s.change_percent > 0 ? "up" : s.change_percent < 0 ? "down" : "flat";
    return `
      <div class="stock-card" data-stock-card-quick="${s.id}">
        <div class="stock-card-head">
          <div><div class="stock-symbol">${s.symbol}</div><div class="stock-name">${s.name}</div></div>
          <div class="stock-change ${cls}">${formatPct(s.change_percent)}</div>
        </div>
        <div class="stock-price">${formatMoney(s.price)}</div>
        <div class="stock-actions"><button class="btn btn-primary btn-sm w-full" style="margin-top:0;">Operar</button></div>
      </div>`;
  }).join("");
  top.forEach((s) => {
    grid.querySelector(`[data-stock-card-quick="${s.id}"]`).addEventListener("click", () => openTradeModal(s.id));
  });
}

/* --------------------------------------------------------------------
   Modal de compra / venta
   -------------------------------------------------------------------- */
async function openTradeModal(stockId) {
  activeTradeStock = currentStocks.find((s) => s.id === stockId);
  if (!activeTradeStock) return;

  document.getElementById("tradeModalTitle").textContent = `${activeTradeStock.name} (${activeTradeStock.symbol})`;
  document.getElementById("tradeModalSub").textContent = "Elige comprar o vender y define la cantidad.";
  document.getElementById("tradeQuantityInput").value = "";

  setTradeMode("buy");
  document.getElementById("tradeModalOverlay").classList.add("show");

  const res = await apiFetch(`/api/stocks/${stockId}/history?limit=40`);
  const prices = res.ok ? res.history.map((h) => h.price) : [activeTradeStock.price];

  if (tradeChart) tradeChart.destroy();
  tradeChart = new Chart(document.getElementById("tradeChartCanvas"), {
    type: "line",
    data: {
      labels: prices.map((_, i) => i),
      datasets: [{
        data: prices,
        borderColor: "#3d7fff",
        backgroundColor: "rgba(61,127,255,0.12)",
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { display: false },
        y: { ticks: { color: "#8fa3c0" }, grid: { color: "rgba(255,255,255,0.05)" } },
      },
      plugins: { legend: { display: false } },
    },
  });

  await updateTradeSummary(true);
}

function closeTradeModal() {
  document.getElementById("tradeModalOverlay").classList.remove("show");
  activeTradeStock = null;
}

function setTradeMode(mode) {
  tradeMode = mode;
  const buyTab = document.getElementById("buyTabBtn");
  const sellTab = document.getElementById("sellTabBtn");
  const confirmBtn = document.getElementById("confirmTradeBtn");

  buyTab.classList.toggle("active", mode === "buy");
  sellTab.classList.toggle("active", mode === "sell");

  if (mode === "buy") {
    confirmBtn.className = "btn btn-buy w-full mt-16";
    confirmBtn.style.padding = "14px";
    confirmBtn.textContent = "Confirmar compra";
  } else {
    confirmBtn.className = "btn btn-sell w-full mt-16";
    confirmBtn.style.padding = "14px";
    confirmBtn.textContent = "Confirmar venta";
  }
  updateTradeSummary();
}

async function updateTradeSummary(fetchOwned = false) {
  if (!activeTradeStock) return;
  const qty = parseFloat(document.getElementById("tradeQuantityInput").value) || 0;
  const price = activeTradeStock.price;

  document.getElementById("tradeSummaryPrice").textContent = formatMoney(price);
  document.getElementById("tradeSummaryTotal").textContent = formatMoney(price * qty);

  if (fetchOwned) {
    const res = await apiFetch("/api/portfolio");
    if (res.ok) {
      const pos = res.summary.positions.find((p) => p.stock_id === activeTradeStock.id);
      document.getElementById("tradeSummaryOwned").textContent = pos ? formatQty(pos.quantity) : 0;
      document.getElementById("tradeSummaryOwned").dataset.owned = pos ? pos.quantity : 0;
    }
  }
}

async function confirmTrade() {
  if (!activeTradeStock) return;
  const qty = parseFloat(document.getElementById("tradeQuantityInput").value);

  if (!qty || qty <= 0) {
    showToast("Ingresa una cantidad válida.", "error");
    return;
  }

  const endpoint = tradeMode === "buy" ? "/api/trade/buy" : "/api/trade/sell";
  const btn = document.getElementById("confirmTradeBtn");
  btn.disabled = true;

  const res = await apiFetch(endpoint, { method: "POST", body: { stock_id: activeTradeStock.id, quantity: qty } });
  btn.disabled = false;

  if (!res.ok) {
    showToast(res.error || "No se pudo completar la operación.", "error");
    playErrorSound();
    return;
  }

  showToast(res.message, "success");
  playSuccessSound();
  closeTradeModal();
  refreshEverything();
}

/* --------------------------------------------------------------------
   Portafolio
   -------------------------------------------------------------------- */
async function loadPortfolio() {
  const res = await apiFetch("/api/portfolio");
  if (!res.ok) return;
  const s = res.summary;

  document.getElementById("portfolioStats").innerHTML = `
    <div class="stat-card"><div class="label">Dinero disponible</div><div class="value">${formatMoney(s.available_cash)}</div></div>
    <div class="stat-card"><div class="label">Dinero invertido</div><div class="value">${formatMoney(s.invested)}</div></div>
    <div class="stat-card"><div class="label">Valor actual de posiciones</div><div class="value">${formatMoney(s.current_value)}</div></div>
    <div class="stat-card"><div class="label">Patrimonio total</div><div class="value">${formatMoney(s.equity)}</div></div>
  `;

  const wrap = document.getElementById("portfolioTableWrap");
  if (s.positions.length === 0) {
    wrap.innerHTML = `<div class="empty-state"><div class="big-icon">📊</div>Aún no tienes acciones. Ve a "Mercado" para comprar tu primera posición.</div>`;
    return;
  }

  wrap.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Empresa</th><th>Cantidad</th><th>Precio promedio</th><th>Precio actual</th>
          <th>Valor actual</th><th>Rendimiento</th><th></th>
        </tr>
      </thead>
      <tbody>
        ${s.positions.map((p) => `
          <tr>
            <td><b>${p.symbol}</b> · ${p.name}</td>
            <td>${formatQty(p.quantity)}</td>
            <td>${formatMoney(p.avg_price)}</td>
            <td>${formatMoney(p.current_price)}</td>
            <td>${formatMoney(p.current_value)}</td>
            <td class="${p.profit_loss >= 0 ? 'text-green' : 'text-red'}">${formatMoney(p.profit_loss)} (${formatPct(p.profit_pct)})</td>
            <td><button class="btn btn-ghost btn-sm" data-sell-position="${p.stock_id}">Operar</button></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;

  s.positions.forEach((p) => {
    wrap.querySelector(`[data-sell-position="${p.stock_id}"]`).addEventListener("click", () => openTradeModal(p.stock_id));
  });
}

/* --------------------------------------------------------------------
   Historial
   -------------------------------------------------------------------- */
async function loadHistory() {
  const res = await apiFetch("/api/history");
  if (!res.ok) return;
  const wrap = document.getElementById("historyTableWrap");

  if (res.history.length === 0) {
    wrap.innerHTML = `<div class="empty-state"><div class="big-icon">🧾</div>Todavía no has realizado ninguna operación.</div>`;
    return;
  }

  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Fecha</th><th>Empresa</th><th>Tipo</th><th>Cantidad</th><th>Precio</th><th>Ganancia/Pérdida</th></tr></thead>
      <tbody>
        ${res.history.map((h) => `
          <tr>
            <td>${h.timestamp}</td>
            <td>${h.symbol} · ${h.name}</td>
            <td>${h.type === 'buy' ? '<span class="badge-buy">COMPRA</span>' : '<span class="badge-sell">VENTA</span>'}</td>
            <td>${formatQty(h.quantity)}</td>
            <td>${formatMoney(h.price)}</td>
            <td class="${(h.profit_loss ?? 0) >= 0 ? 'text-green' : 'text-red'}">${h.profit_loss !== null ? formatMoney(h.profit_loss) : '—'}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

/* --------------------------------------------------------------------
   Ranking
   -------------------------------------------------------------------- */
async function loadRanking() {
  const res = await apiFetch("/api/ranking");
  if (!res.ok) return;

  const renderList = (list, valueKey) => list.map((u, i) => `
    <div class="ranking-row">
      <div class="rank-num">${i + 1}</div>
      <div class="rank-name">${u.name}</div>
      <div class="rank-value ${u[valueKey] >= 0 ? 'text-green' : 'text-red'}">${formatMoney(u[valueKey])}</div>
    </div>
  `).join("");

  document.getElementById("rankingByEquity").innerHTML = res.by_equity.length
    ? renderList(res.by_equity, "equity")
    : `<div class="empty-state">Aún no hay inversionistas.</div>`;

  document.getElementById("rankingByGains").innerHTML = res.by_gains.length
    ? renderList(res.by_gains, "gains")
    : `<div class="empty-state">Aún no hay inversionistas.</div>`;
}
