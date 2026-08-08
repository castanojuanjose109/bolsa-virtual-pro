/* ==========================================================================
   admin.js — Lógica del panel de administración oculto (/admin).
   ========================================================================== */

let adminStocksCache = [];
let editingUserId = null;
let editingStockId = null;
let adminRefreshTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  bindAdminLoginEvents();
  bindAdminAppEvents();
  checkAdminSession();
});

/* --------------------------------------------------------------------
   Sesión y login de administrador
   -------------------------------------------------------------------- */
function bindAdminLoginEvents() {
  document.getElementById("adminLoginBtn").addEventListener("click", doAdminLogin);
  document.getElementById("adminPassInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doAdminLogin();
  });
}

async function checkAdminSession() {
  const res = await apiFetch("/admin/api/session");
  if (res.ok && res.is_admin) {
    enterAdminPanel();
  }
}

async function doAdminLogin() {
  const username = document.getElementById("adminUserInput").value.trim();
  const password = document.getElementById("adminPassInput").value;
  const errorBox = document.getElementById("adminLoginError");
  errorBox.classList.remove("show");

  const res = await apiFetch("/admin/api/login", { method: "POST", body: { username, password } });
  if (!res.ok) {
    errorBox.textContent = res.error || "Credenciales incorrectas.";
    errorBox.classList.add("show");
    return;
  }
  enterAdminPanel();
}

function enterAdminPanel() {
  document.getElementById("adminLoginScreen").classList.add("hidden");
  document.getElementById("adminShell").classList.remove("hidden");
  refreshAdminOverview();
  loadAdminUsers();
  loadAdminStocks();
  loadAdminDeposits();
  loadMarketSchedule();
  loadCdtRate();
  loadAdminCdtList();
  loadMassPaymentSchedule();
  loadMassPaymentHistory();
  adminRefreshTimer = setInterval(() => {
    refreshAdminOverview();
    if (document.getElementById("adminMarketView").classList.contains("active")) { loadAdminStocks(); loadMarketSchedule(); }
    if (document.getElementById("adminDepositsView").classList.contains("active")) loadAdminDeposits();
    if (document.getElementById("adminCdtView").classList.contains("active")) loadAdminCdtList();
    if (document.getElementById("adminMassPaymentView").classList.contains("active")) { loadMassPaymentSchedule(); loadMassPaymentHistory(); }
  }, 4000);
}

async function doAdminLogout() {
  await apiFetch("/admin/api/logout", { method: "POST" });
  clearInterval(adminRefreshTimer);
  window.location.reload();
}

/* --------------------------------------------------------------------
   Eventos generales
   -------------------------------------------------------------------- */
function bindAdminAppEvents() {
  document.getElementById("adminLogoutBtn").addEventListener("click", doAdminLogout);

  document.querySelectorAll(".admin-tabs .tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      document.getElementById(btn.dataset.view).classList.add("active");
      document.querySelectorAll(".admin-tabs .tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      if (btn.dataset.view === "adminUsersView") loadAdminUsers();
      if (btn.dataset.view === "adminMarketView") { loadAdminStocks(); loadMarketSchedule(); }
      if (btn.dataset.view === "adminDepositsView") loadAdminDeposits();
      if (btn.dataset.view === "adminCdtView") { loadCdtRate(); loadAdminCdtList(); }
      if (btn.dataset.view === "adminMassPaymentView") { loadMassPaymentSchedule(); loadMassPaymentHistory(); }
    });
  });

  document.getElementById("startMarketBtn").addEventListener("click", async () => {
    const res = await apiFetch("/admin/api/market/start", { method: "POST" });
    if (res.ok) { showToast("Mercado automático iniciado.", "success"); refreshAdminOverview(); }
  });
  document.getElementById("stopMarketBtn").addEventListener("click", async () => {
    const res = await apiFetch("/admin/api/market/stop", { method: "POST" });
    if (res.ok) { showToast("Mercado automático detenido.", "success"); refreshAdminOverview(); }
  });

  // Horario programado del mercado
  document.getElementById("saveScheduleBtn").addEventListener("click", saveMarketSchedule);
  document.getElementById("enableScheduleBtn").addEventListener("click", () => toggleMarketSchedule(true));
  document.getElementById("disableScheduleBtn").addEventListener("click", () => toggleMarketSchedule(false));

  // Tasa de CDT
  document.getElementById("saveCdtRateBtn").addEventListener("click", saveCdtRate);

  // Pagos masivos
  document.getElementById("sendMassPaymentNowBtn").addEventListener("click", sendMassPaymentNow);
  document.getElementById("saveMassPaymentScheduleBtn").addEventListener("click", saveMassPaymentSchedule);
  document.getElementById("enableMassPaymentBtn").addEventListener("click", () => toggleMassPaymentSchedule(true));
  document.getElementById("disableMassPaymentBtn").addEventListener("click", () => toggleMassPaymentSchedule(false));

  document.getElementById("createUserBtn").addEventListener("click", createUser);

  // Modal edición de usuario
  document.getElementById("closeEditUserModalBtn").addEventListener("click", closeEditUserModal);
  document.getElementById("editUserModalOverlay").addEventListener("click", (e) => {
    if (e.target.id === "editUserModalOverlay") closeEditUserModal();
  });
  document.getElementById("saveUserBtn").addEventListener("click", saveUserChanges);
  document.getElementById("addMoneyBtn").addEventListener("click", () => adjustMoney(1));
  document.getElementById("removeMoneyBtn").addEventListener("click", () => adjustMoney(-1));
  document.getElementById("giveStockBtn").addEventListener("click", () => adjustStock("give"));
  document.getElementById("takeStockBtn").addEventListener("click", () => adjustStock("take"));
  document.getElementById("blockUserBtn").addEventListener("click", () => userAction("block"));
  document.getElementById("unblockUserBtn").addEventListener("click", () => userAction("unblock"));
  document.getElementById("resetUserBtn").addEventListener("click", () => userAction("reset", true));
  document.getElementById("deleteUserBtn").addEventListener("click", () => userAction("delete", true));

  // Modal edición de acción
  document.getElementById("closeEditStockModalBtn").addEventListener("click", closeEditStockModal);
  document.getElementById("editStockModalOverlay").addEventListener("click", (e) => {
    if (e.target.id === "editStockModalOverlay") closeEditStockModal();
  });
  document.getElementById("saveStockBtn").addEventListener("click", saveStockChanges);
  document.querySelectorAll(".quick-pct").forEach((btn) => {
    btn.addEventListener("click", () => applyStockPercent(parseFloat(btn.dataset.pct)));
  });
  document.getElementById("applyManualPctBtn").addEventListener("click", () => {
    const val = parseFloat(document.getElementById("manualPctInput").value);
    if (!isNaN(val)) applyStockPercent(val);
  });
}

/* --------------------------------------------------------------------
   Resumen global
   -------------------------------------------------------------------- */
async function refreshAdminOverview() {
  const res = await apiFetch("/admin/api/stats");
  if (!res.ok) return;

  document.getElementById("marketStatusLabel").textContent = res.market_running ? "activo 🟢" : "detenido 🔴";

  const badge = document.getElementById("pendingDepositsBadge");
  if (badge) badge.textContent = res.pending_money_requests > 0 ? `(${res.pending_money_requests})` : "";

  document.getElementById("adminStatsGrid").innerHTML = `
    <div class="stat-card"><div class="label">Usuarios registrados</div><div class="value">${res.total_users}</div></div>
    <div class="stat-card"><div class="label">Operaciones totales</div><div class="value">${res.total_trades}</div></div>
    <div class="stat-card"><div class="label">Dinero en circulación</div><div class="value">${formatMoney(res.total_balance)}</div></div>
    <div class="stat-card"><div class="label">Acciones activas</div><div class="value">${res.active_stocks}</div></div>
    <div class="stat-card"><div class="label">Solicitudes pendientes</div><div class="value ${res.pending_money_requests > 0 ? 'negative' : ''}">${res.pending_money_requests}</div></div>
  `;
}

/* --------------------------------------------------------------------
   Solicitudes de dinero ficticio (depósito y retiro)
   -------------------------------------------------------------------- */
async function loadAdminDeposits() {
  const res = await apiFetch("/admin/api/money-requests");
  if (!res.ok) return;
  const wrap = document.getElementById("adminDepositsTableWrap");

  if (res.requests.length === 0) {
    wrap.innerHTML = `<div class="empty-state">No hay solicitudes de dinero todavía.</div>`;
    return;
  }

  const statusLabel = { pending: "⏳ Pendiente", approved: "✅ Aprobado", rejected: "❌ Rechazado" };
  const typeLabel = { deposit: "🟢 Depósito", withdraw: "🔴 Retiro" };

  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Fecha</th><th>Usuario</th><th>Tipo</th><th>Monto</th><th>Motivo</th><th>Estado</th><th></th></tr></thead>
      <tbody>
        ${res.requests.map((r) => `
          <tr>
            <td>${r.requested_at}</td>
            <td>${r.user_name}</td>
            <td>${typeLabel[r.type] || r.type}</td>
            <td>${formatMoney(r.amount)}</td>
            <td>${r.note || "—"}</td>
            <td>${statusLabel[r.status]}</td>
            <td>
              ${r.status === "pending" ? `
                <div class="action-row">
                  <button class="btn btn-outline-green btn-sm" data-approve-deposit="${r.id}">Aprobar</button>
                  <button class="btn btn-outline-red btn-sm" data-reject-deposit="${r.id}">Rechazar</button>
                </div>
              ` : "—"}
            </td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;

  res.requests.filter((r) => r.status === "pending").forEach((r) => {
    wrap.querySelector(`[data-approve-deposit="${r.id}"]`).addEventListener("click", () => resolveDeposit(r.id, "approve"));
    wrap.querySelector(`[data-reject-deposit="${r.id}"]`).addEventListener("click", () => resolveDeposit(r.id, "reject"));
  });
}

async function resolveDeposit(requestId, action) {
  const res = await apiFetch(`/admin/api/money-requests/${requestId}/${action}`, { method: "POST" });
  if (!res.ok) { showToast(res.error || "No se pudo procesar la solicitud.", "error"); return; }
  showToast(action === "approve" ? "Solicitud aprobada y saldo actualizado." : "Solicitud rechazada.", "success");
  loadAdminDeposits();
  refreshAdminOverview();
  loadAdminUsers();
}

/* --------------------------------------------------------------------
   Gestión de usuarios
   -------------------------------------------------------------------- */
async function createUser() {
  const name = document.getElementById("newUserName").value.trim();
  const balance = document.getElementById("newUserBalance").value;
  const password = document.getElementById("newUserPassword").value.trim();
  if (!name) { showToast("Escribe un nombre para el nuevo usuario.", "error"); return; }

  const body = { name };
  if (balance) body.balance = parseFloat(balance);
  if (password) body.password = password;

  const res = await apiFetch("/admin/api/users/create", { method: "POST", body });
  if (!res.ok) { showToast(res.error || "No se pudo crear el usuario.", "error"); return; }

  if (res.generated_password) {
    showToast(`Usuario creado. Contraseña generada: ${res.generated_password}`, "success");
  } else {
    showToast("Usuario creado correctamente.", "success");
  }
  document.getElementById("newUserName").value = "";
  document.getElementById("newUserBalance").value = "";
  document.getElementById("newUserPassword").value = "";
  loadAdminUsers();
}

async function loadAdminUsers() {
  const res = await apiFetch("/admin/api/users");
  if (!res.ok) return;
  const wrap = document.getElementById("adminUsersTableWrap");

  if (res.users.length === 0) {
    wrap.innerHTML = `<div class="empty-state">Todavía no hay usuarios registrados.</div>`;
    return;
  }

  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Nombre</th><th>Saldo</th><th>Invertido</th><th>Patrimonio</th><th>Estado</th><th></th></tr></thead>
      <tbody>
        ${res.users.map((u) => `
          <tr>
            <td>${u.name}</td>
            <td>${formatMoney(u.balance)}</td>
            <td>${formatMoney(u.invested)}</td>
            <td>${formatMoney(u.equity)}</td>
            <td>${u.is_blocked ? '<span class="badge-sell">BLOQUEADO</span>' : '<span class="badge-buy">ACTIVO</span>'}</td>
            <td><button class="btn btn-ghost btn-sm" data-edit-user="${u.id}">Gestionar</button></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;

  res.users.forEach((u) => {
    wrap.querySelector(`[data-edit-user="${u.id}"]`).addEventListener("click", () => openEditUserModal(u));
  });
}

async function openEditUserModal(user) {
  editingUserId = user.id;
  document.getElementById("editUserSub").textContent = `ID #${user.id} — creado el ${user.created_at || "—"}`;
  document.getElementById("editUserName").value = user.name;
  document.getElementById("editUserBalance").value = user.balance;
  document.getElementById("moneyAmountInput").value = "";
  document.getElementById("stockQtyForUser").value = "";

  // Poblar selector de acciones disponibles
  const select = document.getElementById("stockSelectForUser");
  select.innerHTML = adminStocksCache.map((s) => `<option value="${s.id}">${s.symbol} · ${s.name}</option>`).join("");

  document.getElementById("editUserModalOverlay").classList.add("show");
}

function closeEditUserModal() {
  document.getElementById("editUserModalOverlay").classList.remove("show");
  editingUserId = null;
}

async function saveUserChanges() {
  const name = document.getElementById("editUserName").value.trim();
  const balance = document.getElementById("editUserBalance").value;
  const newPassword = document.getElementById("editUserNewPassword").value.trim();

  const body = { name, balance: parseFloat(balance) };
  if (newPassword) body.new_password = newPassword;

  const res = await apiFetch(`/admin/api/users/${editingUserId}/update`, { method: "POST", body });
  if (!res.ok) { showToast(res.error || "No se pudo actualizar el usuario.", "error"); return; }
  showToast("Usuario actualizado.", "success");
  document.getElementById("editUserNewPassword").value = "";
  loadAdminUsers();
  closeEditUserModal();
}

async function adjustMoney(direction) {
  const amount = parseFloat(document.getElementById("moneyAmountInput").value);
  if (!amount || amount <= 0) { showToast("Ingresa un monto válido.", "error"); return; }
  const endpoint = direction > 0 ? "add-money" : "remove-money";
  const res = await apiFetch(`/admin/api/users/${editingUserId}/${endpoint}`, { method: "POST", body: { amount } });
  if (!res.ok) { showToast(res.error || "No se pudo aplicar el cambio.", "error"); return; }
  showToast("Saldo actualizado.", "success");
  loadAdminUsers();
}

async function adjustStock(action) {
  const stockId = parseInt(document.getElementById("stockSelectForUser").value, 10);
  const quantity = parseFloat(document.getElementById("stockQtyForUser").value);
  if (!quantity || quantity <= 0) { showToast("Ingresa una cantidad válida.", "error"); return; }

  const endpoint = action === "give" ? "give-stock" : "take-stock";
  const res = await apiFetch(`/admin/api/users/${editingUserId}/${endpoint}`, {
    method: "POST",
    body: { stock_id: stockId, quantity },
  });
  if (!res.ok) { showToast(res.error || "No se pudo aplicar el cambio.", "error"); return; }
  showToast(action === "give" ? "Acciones regaladas." : "Acciones retiradas.", "success");
  loadAdminUsers();
}

async function userAction(action, needsConfirm = false) {
  if (needsConfirm) {
    const messages = {
      reset: "¿Reiniciar esta cuenta? Se perderá su historial y posiciones.",
      delete: "¿Eliminar esta cuenta de forma permanente?",
    };
    if (!confirm(messages[action] || "¿Confirmar acción?")) return;
  }
  const res = await apiFetch(`/admin/api/users/${editingUserId}/${action}`, { method: "POST" });
  if (!res.ok) { showToast(res.error || "No se pudo completar la acción.", "error"); return; }
  showToast("Acción aplicada correctamente.", "success");
  loadAdminUsers();
  if (action === "delete") closeEditUserModal();
}

/* --------------------------------------------------------------------
   Gestión del mercado
   -------------------------------------------------------------------- */
async function loadAdminStocks() {
  const res = await apiFetch("/admin/api/stocks");
  if (!res.ok) return;
  adminStocksCache = res.stocks;
  const wrap = document.getElementById("adminStocksTableWrap");

  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Símbolo</th><th>Nombre</th><th>Precio</th><th>Cambio</th><th>Estado</th><th></th></tr></thead>
      <tbody>
        ${adminStocksCache.map((s) => `
          <tr>
            <td><b>${s.symbol}</b></td>
            <td>${s.name}</td>
            <td>${formatMoney(s.price)}</td>
            <td class="${s.change_percent >= 0 ? 'text-green' : 'text-red'}">${formatPct(s.change_percent)}</td>
            <td>${s.status === 'active' ? '<span class="badge-buy">ACTIVA</span>' : '<span class="badge-sell">INACTIVA</span>'}</td>
            <td><button class="btn btn-ghost btn-sm" data-edit-stock="${s.id}">Editar</button></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;

  adminStocksCache.forEach((s) => {
    wrap.querySelector(`[data-edit-stock="${s.id}"]`).addEventListener("click", () => openEditStockModal(s));
  });
}

function openEditStockModal(stock) {
  editingStockId = stock.id;
  document.getElementById("editStockSub").textContent = `${stock.symbol} — ${stock.name}`;
  document.getElementById("editStockName").value = stock.name;
  document.getElementById("editStockPrice").value = stock.price;
  document.getElementById("editStockChange").value = stock.change_percent;
  document.getElementById("editStockStatus").value = stock.status;
  document.getElementById("manualPctInput").value = "";
  document.getElementById("editStockModalOverlay").classList.add("show");
}

function closeEditStockModal() {
  document.getElementById("editStockModalOverlay").classList.remove("show");
  editingStockId = null;
}

async function saveStockChanges() {
  const body = {
    name: document.getElementById("editStockName").value.trim(),
    price: parseFloat(document.getElementById("editStockPrice").value),
    change_percent: parseFloat(document.getElementById("editStockChange").value),
    status: document.getElementById("editStockStatus").value,
  };
  const res = await apiFetch(`/admin/api/stocks/${editingStockId}/update`, { method: "POST", body });
  if (!res.ok) { showToast(res.error || "No se pudo actualizar la acción.", "error"); return; }
  showToast("Acción actualizada.", "success");
  loadAdminStocks();
  closeEditStockModal();
}

async function applyStockPercent(percent) {
  const res = await apiFetch(`/admin/api/stocks/${editingStockId}/adjust`, { method: "POST", body: { percent } });
  if (!res.ok) { showToast(res.error || "No se pudo aplicar el cambio.", "error"); return; }
  showToast(`Precio ajustado ${percent > 0 ? "+" : ""}${percent}%.`, "success");
  document.getElementById("editStockPrice").value = res.new_price;
  document.getElementById("editStockChange").value = percent;
  loadAdminStocks();
}

/* --------------------------------------------------------------------
   Horario programado del mercado automático
   -------------------------------------------------------------------- */
async function loadMarketSchedule() {
  const res = await apiFetch("/admin/api/market/schedule");
  if (!res.ok) return;
  document.getElementById("scheduleStartHour").value = res.start_hour;
  document.getElementById("scheduleEndHour").value = res.end_hour;
  document.getElementById("scheduleServerTime").textContent = res.server_time;
  document.getElementById("scheduleStatusLabel").textContent = res.enabled ? "activo 🟢" : "desactivado 🔴";
}

async function saveMarketSchedule() {
  const startHour = parseInt(document.getElementById("scheduleStartHour").value, 10);
  const endHour = parseInt(document.getElementById("scheduleEndHour").value, 10);
  if (isNaN(startHour) || isNaN(endHour) || startHour < 0 || startHour > 23 || endHour < 0 || endHour > 23) {
    showToast("Las horas deben estar entre 0 y 23.", "error");
    return;
  }
  const current = await apiFetch("/admin/api/market/schedule");
  const res = await apiFetch("/admin/api/market/schedule", {
    method: "POST",
    body: { enabled: current.enabled, start_hour: startHour, end_hour: endHour },
  });
  if (!res.ok) { showToast(res.error || "No se pudo guardar el horario.", "error"); return; }
  showToast("Horario guardado.", "success");
  loadMarketSchedule();
}

async function toggleMarketSchedule(enabled) {
  const startHour = parseInt(document.getElementById("scheduleStartHour").value, 10) || 9;
  const endHour = parseInt(document.getElementById("scheduleEndHour").value, 10) || 18;
  const res = await apiFetch("/admin/api/market/schedule", {
    method: "POST",
    body: { enabled, start_hour: startHour, end_hour: endHour },
  });
  if (!res.ok) { showToast(res.error || "No se pudo aplicar el cambio.", "error"); return; }
  showToast(enabled ? "Horario programado activado." : "Horario programado desactivado.", "success");
  loadMarketSchedule();
  refreshAdminOverview();
}

/* --------------------------------------------------------------------
   Tasa de interés del CDT
   -------------------------------------------------------------------- */
async function loadCdtRate() {
  const res = await apiFetch("/admin/api/cdt/rate");
  if (!res.ok) return;
  document.getElementById("cdtRateInput").value = res.rate_percent;
}

async function saveCdtRate() {
  const rate = parseFloat(document.getElementById("cdtRateInput").value);
  if (isNaN(rate) || rate < 0 || rate > 100) { showToast("El porcentaje debe estar entre 0 y 100.", "error"); return; }
  const res = await apiFetch("/admin/api/cdt/rate", { method: "POST", body: { rate_percent: rate } });
  if (!res.ok) { showToast(res.error || "No se pudo guardar la tasa.", "error"); return; }
  showToast("Tasa de CDT actualizada. Aplica solo a los CDTs nuevos.", "success");
}

async function loadAdminCdtList() {
  const res = await apiFetch("/admin/api/cdt/list");
  if (!res.ok) return;
  const wrap = document.getElementById("adminCdtTableWrap");

  if (res.cdts.length === 0) {
    wrap.innerHTML = `<div class="empty-state">Todavía no hay ningún CDT abierto.</div>`;
    return;
  }

  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Usuario</th><th>Monto</th><th>Tasa</th><th>Progreso</th><th>Próximo pago</th><th>Estado</th></tr></thead>
      <tbody>
        ${res.cdts.map((c) => `
          <tr>
            <td>${c.user_name}</td>
            <td>${formatMoney(c.amount)}</td>
            <td>${c.rate_percent}%</td>
            <td>${c.quincenas_pagadas} / ${c.quincenas_total}</td>
            <td>${c.status === 'active' ? c.next_payment_at : '—'}</td>
            <td>${c.status === 'active' ? '<span class="badge-buy">ACTIVO</span>' : '<span class="badge-sell">COMPLETADO</span>'}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

/* --------------------------------------------------------------------
   Pagos masivos a todos los usuarios
   -------------------------------------------------------------------- */
async function sendMassPaymentNow() {
  const amount = parseFloat(document.getElementById("massPaymentAmountNow").value);
  if (!amount || amount <= 0) { showToast("Ingresa un monto válido.", "error"); return; }
  if (!confirm(`¿Enviar ${formatMoney(amount)} a TODOS los usuarios registrados ahora mismo?`)) return;

  const btn = document.getElementById("sendMassPaymentNowBtn");
  btn.disabled = true;
  const res = await apiFetch("/admin/api/mass-payment/send-now", { method: "POST", body: { amount } });
  btn.disabled = false;

  if (!res.ok) { showToast(res.error || "No se pudo enviar el pago.", "error"); return; }
  showToast(`Se envió ${formatMoney(amount)} a ${res.users_count} usuario(s).`, "success");
  document.getElementById("massPaymentAmountNow").value = "";
  loadMassPaymentHistory();
  refreshAdminOverview();
}

async function loadMassPaymentSchedule() {
  const res = await apiFetch("/admin/api/mass-payment/schedule");
  if (!res.ok) return;
  document.getElementById("massPaymentRecurringAmount").value = res.amount || "";
  document.getElementById("massPaymentIntervalHours").value = res.interval_hours || 24;
  document.getElementById("massPaymentStatusLabel").textContent = res.enabled ? "activo 🟢" : "desactivado 🔴";
  document.getElementById("massPaymentLastRunLabel").textContent = res.last_run || "nunca";
}

async function saveMassPaymentSchedule() {
  const amount = parseFloat(document.getElementById("massPaymentRecurringAmount").value);
  const intervalHours = parseFloat(document.getElementById("massPaymentIntervalHours").value);
  if (!amount || amount <= 0) { showToast("Ingresa un monto válido.", "error"); return; }
  if (!intervalHours || intervalHours <= 0) { showToast("Ingresa un intervalo válido en horas.", "error"); return; }

  const current = await apiFetch("/admin/api/mass-payment/schedule");
  const res = await apiFetch("/admin/api/mass-payment/schedule", {
    method: "POST",
    body: { enabled: current.enabled, amount, interval_hours: intervalHours },
  });
  if (!res.ok) { showToast(res.error || "No se pudo guardar.", "error"); return; }
  showToast("Configuración de pago recurrente guardada.", "success");
  loadMassPaymentSchedule();
}

async function toggleMassPaymentSchedule(enabled) {
  const amount = parseFloat(document.getElementById("massPaymentRecurringAmount").value);
  const intervalHours = parseFloat(document.getElementById("massPaymentIntervalHours").value);
  if (enabled && (!amount || amount <= 0)) { showToast("Ingresa un monto válido antes de activar.", "error"); return; }
  if (enabled && (!intervalHours || intervalHours <= 0)) { showToast("Ingresa un intervalo válido antes de activar.", "error"); return; }

  const res = await apiFetch("/admin/api/mass-payment/schedule", {
    method: "POST",
    body: { enabled, amount: amount || 0, interval_hours: intervalHours || 24 },
  });
  if (!res.ok) { showToast(res.error || "No se pudo aplicar el cambio.", "error"); return; }
  showToast(enabled ? "Pago recurrente activado." : "Pago recurrente desactivado.", "success");
  loadMassPaymentSchedule();
}

async function loadMassPaymentHistory() {
  const res = await apiFetch("/admin/api/mass-payment/history");
  if (!res.ok) return;
  const wrap = document.getElementById("massPaymentHistoryWrap");

  if (res.payments.length === 0) {
    wrap.innerHTML = `<div class="empty-state">Todavía no se ha enviado ningún pago masivo.</div>`;
    return;
  }

  const sourceLabel = { manual: "Manual", recurring: "Recurrente" };
  wrap.innerHTML = `
    <table class="data-table">
      <thead><tr><th>Fecha</th><th>Monto por usuario</th><th>Usuarios</th><th>Tipo</th></tr></thead>
      <tbody>
        ${res.payments.map((p) => `
          <tr>
            <td>${p.paid_at}</td>
            <td>${formatMoney(p.amount)}</td>
            <td>${p.users_count}</td>
            <td>${sourceLabel[p.source] || p.source}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}
