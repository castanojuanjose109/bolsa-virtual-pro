/* ==========================================================================
   app.js — Utilidades compartidas entre el dashboard y el panel de admin.
   No depende de ninguna librería ni servicio externo.
   ========================================================================== */

/** Realiza una petición a la API y devuelve el JSON parseado. */
async function apiFetch(url, options = {}) {
  const finalOptions = {
    method: options.method || "GET",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
  };
  if (options.body) finalOptions.body = JSON.stringify(options.body);

  const res = await fetch(url, finalOptions);
  let data;
  try {
    data = await res.json();
  } catch (e) {
    data = { ok: false, error: "Respuesta inválida del servidor." };
  }
  if (!res.ok && data.ok === undefined) data.ok = false;
  return data;
}

/** Formatea un número como dinero en dólares ficticios. */
function formatMoney(value) {
  const n = Number(value || 0);
  return "$" + n.toLocaleString("es-CO", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Formatea un porcentaje con signo. */
function formatPct(value) {
  const n = Number(value || 0);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

/** Formatea una cantidad de acciones (permite fracciones como 0.1 o 0.5,
 *  recortando errores de precisión decimal sin ceros sobrantes). */
function formatQty(value) {
  const n = Number(value || 0);
  return parseFloat(n.toFixed(4)).toString();
}

/** Muestra una notificación flotante (toast). */
function showToast(message, type = "info") {
  const stack = document.getElementById("toastStack");
  if (!stack) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  stack.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity 300ms ease";
    el.style.opacity = "0";
    setTimeout(() => el.remove(), 300);
  }, 3800);
}

/* --------------------------------------------------------------------
   Tema claro / oscuro
   -------------------------------------------------------------------- */
function initTheme() {
  const saved = localStorageSafeGet("bvp_theme") || "dark";
  applyTheme(saved);
}

function applyTheme(theme) {
  if (theme === "light") {
    document.documentElement.setAttribute("data-theme", "light");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  const btn = document.getElementById("themeToggleBtn");
  if (btn) btn.textContent = theme === "light" ? "☀️" : "🌙";
  localStorageSafeSet("bvp_theme", theme);
}

function toggleTheme() {
  const isLight = document.documentElement.getAttribute("data-theme") === "light";
  applyTheme(isLight ? "dark" : "light");
}

/* localStorage puede no estar disponible en algunos entornos embebidos;
   estas funciones evitan que la app se rompa si eso ocurre. */
function localStorageSafeGet(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}
function localStorageSafeSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* no-op */ }
}

/* --------------------------------------------------------------------
   Sonido opcional (generado con Web Audio API, sin archivos externos)
   -------------------------------------------------------------------- */
let soundEnabled = (localStorageSafeGet("bvp_sound") || "on") === "on";

function initSoundToggleButton() {
  const btn = document.getElementById("soundToggleBtn");
  if (!btn) return;
  btn.textContent = soundEnabled ? "🔔" : "🔕";
  btn.addEventListener("click", () => {
    soundEnabled = !soundEnabled;
    localStorageSafeSet("bvp_sound", soundEnabled ? "on" : "off");
    btn.textContent = soundEnabled ? "🔔" : "🔕";
  });
}

function playTone(freq, duration = 0.12) {
  if (!soundEnabled) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = freq;
    osc.type = "sine";
    gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + duration);
  } catch (e) { /* audio no disponible, ignorar */ }
}

function playSuccessSound() { playTone(880, 0.14); }
function playErrorSound() { playTone(180, 0.22); }

/* --------------------------------------------------------------------
   Utilidad de debounce para el buscador
   -------------------------------------------------------------------- */
function debounce(fn, delay = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}
