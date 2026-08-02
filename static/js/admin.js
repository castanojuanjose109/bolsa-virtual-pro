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
  adminRefreshTimer = setInterval(() => {
    refreshAdminOverview();
    if (document.getElementById("adminMarketView").classList.contains("active")) loadAdminStocks();
    if (document.getElementById("adminDepositsView").classList.contains("active")) loadAdminDeposits();
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
      if (btn.dataset.view === "adminMarketView") loadAdminStocks();
      if (btn.dataset.view === "adminDepositsView") loadAdminDeposits();
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
