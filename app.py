# -*- coding: utf-8 -*-
"""
app.py
------
Servidor principal de Bolsa Virtual Pro (Flask).

Toda la lógica de negocio (saldo, compras, ventas, validaciones, mercado
automático, panel de administración) vive aquí, en el servidor. El
navegador nunca decide precios, saldos ni resultados: solo los muestra.

Ejecutar con:  python app.py
La app queda disponible en http://127.0.0.1:5000
"""

import os
import random
import threading
import time
import io
import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify, session, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash

import database as db

app = Flask(__name__)
# En producción, define la variable de entorno SECRET_KEY con un valor
# aleatorio largo. Si no existe, se usa un valor de respaldo solo para
# desarrollo local.
app.secret_key = os.environ.get("SECRET_KEY", "bolsa-virtual-pro-clave-secreta-cambiar-en-produccion")

# Credenciales fijas del panel de administración (según especificación).
# En producción puedes sobreescribirlas con variables de entorno.
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "123")

# --------------------------------------------------------------------------
# Estado del "mercado automático" en memoria del proceso.
# market_thread se ejecuta en segundo plano moviendo los precios cada 10 s,
# combinando oferta/demanda real (compras y ventas de los usuarios) con
# una pequeña variación aleatoria para simular ruido de mercado.
# --------------------------------------------------------------------------
market_lock = threading.Lock()
market_thread = None
market_running_flag = False

# Acumula la presión de compra/venta (en dinero ficticio) de cada acción
# desde el último "tick" del mercado automático. Cada vez que un usuario
# compra, suma a "buy"; cada vez que vende, suma a "sell". El hilo de
# simulación lee y reinicia estos acumuladores cada 10 segundos.
volume_lock = threading.Lock()
stock_volume = {}  # {stock_id: {"buy": float, "sell": float}}

# Cada MARKET_LIQUIDITY dinero ficticio de compra/venta neta mueve el
# precio un 1%. Un valor más bajo hace que el mercado reaccione más fuerte
# a la actividad de los usuarios (más sensible a la demanda real).
MARKET_LIQUIDITY = 800.0
MARKET_TICK_SECONDS = 10


def register_trade_volume(stock_id, side, amount):
    """Registra presión de compra ('buy') o venta ('sell') sobre una acción."""
    with volume_lock:
        entry = stock_volume.setdefault(stock_id, {"buy": 0.0, "sell": 0.0})
        entry[side] += amount


def take_and_reset_volume(stock_id):
    """Obtiene y reinicia la presión acumulada de una acción para el tick actual."""
    with volume_lock:
        entry = stock_volume.pop(stock_id, None)
    if entry is None:
        return 0.0, 0.0
    return entry["buy"], entry["sell"]


def market_loop():
    """
    Bucle de fondo: mientras el mercado esté activo, cada 10 segundos
    recalcula el precio de cada acción activa combinando dos fuerzas:

    1) Oferta y demanda real: si los usuarios compraron más de lo que
       vendieron desde el último tick, el precio sube; si vendieron más
       de lo que compraron, el precio baja (más dinero neto = más
       movimiento, limitado a ±8%).
    2) Ruido de mercado aleatorio (pequeño, ±2%) para simular la
       volatilidad normal de cualquier bolsa.

    El resultado final siempre queda limitado entre -10% y +10% por tick.
    """
    global market_running_flag
    while True:
        with market_lock:
            running = market_running_flag
        if not running:
            break

        conn = db.get_connection()
        stocks = conn.execute(
            "SELECT id, price FROM stocks WHERE status = 'active'"
        ).fetchall()
        ts = db.now_iso()
        for s in stocks:
            buy_volume, sell_volume = take_and_reset_volume(s["id"])
            net_flow = buy_volume - sell_volume

            demand_pct = (net_flow / MARKET_LIQUIDITY) * 100
            demand_pct = max(-8.0, min(8.0, demand_pct))

            noise_pct = random.uniform(-2, 2)

            pct = max(-10.0, min(10.0, demand_pct + noise_pct))

            new_price = max(0.01, round(s["price"] * (1 + pct / 100), 2))
            conn.execute(
                "UPDATE stocks SET price = ?, change_percent = ? WHERE id = ?",
                (new_price, round(pct, 2), s["id"]),
            )
            conn.execute(
                "INSERT INTO price_history (stock_id, price, timestamp) VALUES (?, ?, ?)",
                (s["id"], new_price, ts),
            )
        conn.commit()
        conn.close()

        time.sleep(MARKET_TICK_SECONDS)


def start_market():
    """Arranca el hilo de simulación de mercado si no está ya corriendo."""
    global market_thread, market_running_flag
    with market_lock:
        if market_running_flag:
            return
        market_running_flag = True
    db.set_config("market_running", "1")
    market_thread = threading.Thread(target=market_loop, daemon=True)
    market_thread.start()


def stop_market():
    """Detiene el hilo de simulación de mercado."""
    global market_running_flag
    with market_lock:
        market_running_flag = False
    db.set_config("market_running", "0")


def is_market_running():
    with market_lock:
        return market_running_flag


# --------------------------------------------------------------------------
# Scheduler global de fondo (un solo hilo, siempre activo desde que arranca
# la app). Cada 30 segundos revisa tres cosas independientes:
#   1) CDTs cuyo pago quincenal (cada 15 días) ya se cumplió.
#   2) Si el mercado automático debe encenderse/apagarse según el horario
#      programado por el admin (hora de inicio / hora de fin).
#   3) Si toca un pago masivo recurrente del admin a todos los usuarios.
# --------------------------------------------------------------------------
SCHEDULER_TICK_SECONDS = 30
CDT_PERIOD_DAYS = 15


def process_due_cdts():
    """Paga el interés quincenal de cada CDT activo cuyo turno ya llegó.
    En el pago final (cuando se cumplen todas las quincenas contratadas),
    además del interés se devuelve el capital y el CDT queda 'completed'.
    """
    conn = db.get_connection()
    now = db.now_dt()
    now_str = db.now_iso()

    due = conn.execute(
        "SELECT * FROM cdts WHERE status = 'active' AND next_payment_at <= ?",
        (now.strftime("%Y-%m-%d %H:%M:%S"),),
    ).fetchall()

    for cdt in due:
        interest = round(cdt["amount"] * (cdt["rate_percent"] / 100), 2)
        new_quincenas_pagadas = cdt["quincenas_pagadas"] + 1
        is_final = new_quincenas_pagadas >= cdt["quincenas_total"]
        principal_returned = cdt["amount"] if is_final else 0.0
        total_credit = round(interest + principal_returned, 2)

        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (total_credit, cdt["user_id"]))

        if is_final:
            conn.execute(
                "UPDATE cdts SET quincenas_pagadas = ?, status = 'completed', completed_at = ? WHERE id = ?",
                (new_quincenas_pagadas, now_str, cdt["id"]),
            )
        else:
            next_payment = (datetime.datetime.strptime(cdt["next_payment_at"], "%Y-%m-%d %H:%M:%S")
                             + datetime.timedelta(days=CDT_PERIOD_DAYS))
            conn.execute(
                "UPDATE cdts SET quincenas_pagadas = ?, next_payment_at = ? WHERE id = ?",
                (new_quincenas_pagadas, next_payment.strftime("%Y-%m-%d %H:%M:%S"), cdt["id"]),
            )

        conn.execute(
            "INSERT INTO cdt_payments (cdt_id, user_id, quincena_numero, interest_amount, principal_returned, paid_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cdt["id"], cdt["user_id"], new_quincenas_pagadas, interest, principal_returned, now_str),
        )

    conn.commit()
    conn.close()


def process_market_schedule():
    """Si el horario programado está activo, enciende o apaga el mercado
    automático según la hora actual (hora de Colombia)."""
    if db.get_config("market_schedule_enabled", "0") != "1":
        return
    try:
        start_hour = int(db.get_config("market_schedule_start_hour", "9"))
        end_hour = int(db.get_config("market_schedule_end_hour", "18"))
    except ValueError:
        return

    current_hour = db.now_dt().hour
    if start_hour <= end_hour:
        should_be_on = start_hour <= current_hour < end_hour
    else:
        # Rango que cruza la medianoche, ej. 22 -> 6
        should_be_on = current_hour >= start_hour or current_hour < end_hour

    if should_be_on and not is_market_running():
        start_market()
    elif not should_be_on and is_market_running():
        stop_market()


def process_mass_payment_schedule():
    """Si el pago masivo recurrente está activo y ya pasaron los días
    configurados desde la última vez, acredita el monto a todos los
    usuarios (o solo a los de un curso, si se configuró un grupo)."""
    if db.get_config("mass_payment_enabled", "0") != "1":
        return
    try:
        amount = float(db.get_config("mass_payment_amount", "0"))
        interval_days = float(db.get_config("mass_payment_interval_days", "1"))
    except ValueError:
        return
    if amount <= 0 or interval_days <= 0:
        return

    target_course = db.get_config("mass_payment_target_course", "")
    last_run_str = db.get_config("mass_payment_last_run", "")
    now = db.now_dt()
    if last_run_str:
        try:
            last_run = datetime.datetime.strptime(last_run_str, "%Y-%m-%d %H:%M:%S")
            if (now - last_run).total_seconds() < interval_days * 86400:
                return
        except ValueError:
            pass

    send_money_to_all_users(amount, source="recurring", target_course=target_course)
    db.set_config("mass_payment_last_run", db.now_iso())


def send_money_to_all_users(amount, source="manual", target_course=""):
    """Acredita 'amount' de dinero ficticio al saldo de todos los usuarios,
    o solo a los de un curso si se indica target_course ('8','9','10','11'),
    deja registro en mass_payments y guarda exactamente quién lo recibió
    (para que cada cuenta lo vea en su propio historial)."""
    conn = db.get_connection()
    user_ids = db.get_user_ids_by_course(target_course)
    if not user_ids:
        conn.close()
        return 0
    now = db.now_iso()
    placeholders = ",".join("?" for _ in user_ids)
    conn.execute(f"UPDATE users SET balance = balance + ? WHERE id IN ({placeholders})", (amount, *user_ids))
    cur = conn.execute(
        "INSERT INTO mass_payments (amount, users_count, source, target_course, paid_at) VALUES (?, ?, ?, ?, ?)",
        (amount, len(user_ids), source, target_course or "", now),
    )
    mass_payment_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO mass_payment_recipients (mass_payment_id, user_id, amount, paid_at) VALUES (?, ?, ?, ?)",
        [(mass_payment_id, uid, amount, now) for uid in user_ids],
    )
    conn.commit()
    conn.close()
    return len(user_ids)


def scheduler_loop():
    """Hilo único de fondo que corre durante toda la vida del proceso."""
    while True:
        try:
            process_due_cdts()
            process_market_schedule()
            process_mass_payment_schedule()
        except Exception as e:
            # Nunca dejamos que un error tumbe el hilo del scheduler.
            print(f"[scheduler_loop] error: {e}")
        time.sleep(SCHEDULER_TICK_SECONDS)


# --------------------------------------------------------------------------
# Decoradores de autenticación
# --------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"ok": False, "error": "No has iniciado sesión."}), 401
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"ok": False, "error": "Acceso de administrador requerido."}), 401
        return f(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------
# Rutas de páginas (frontend)
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


# --------------------------------------------------------------------------
# Helpers de cálculo
# --------------------------------------------------------------------------
def get_stock_or_404(conn, stock_id):
    return conn.execute("SELECT * FROM stocks WHERE id = ?", (stock_id,)).fetchone()


def compute_portfolio(conn, user_id):
    """
    Devuelve (lista_de_posiciones, resumen) para un usuario:
    cada posición trae cantidad, precio promedio, precio actual,
    valor actual y rendimiento %.
    """
    rows = conn.execute("""
        SELECT p.id, p.stock_id, p.quantity, p.avg_price,
               s.symbol, s.name, s.price as current_price, s.change_percent
        FROM portfolios p
        JOIN stocks s ON s.id = p.stock_id
        WHERE p.user_id = ? AND p.quantity > 0
        ORDER BY s.name
    """, (user_id,)).fetchall()

    positions = []
    invested_total = 0.0
    current_value_total = 0.0

    for r in rows:
        invested = r["avg_price"] * r["quantity"]
        current_value = r["current_price"] * r["quantity"]
        profit = current_value - invested
        profit_pct = (profit / invested * 100) if invested > 0 else 0.0

        invested_total += invested
        current_value_total += current_value

        positions.append({
            "portfolio_id": r["id"],
            "stock_id": r["stock_id"],
            "symbol": r["symbol"],
            "name": r["name"],
            "quantity": r["quantity"],
            "avg_price": round(r["avg_price"], 2),
            "current_price": round(r["current_price"], 2),
            "invested": round(invested, 2),
            "current_value": round(current_value, 2),
            "profit_loss": round(profit, 2),
            "profit_pct": round(profit_pct, 2),
            "change_percent": r["change_percent"],
        })

    return positions, invested_total, current_value_total


def compute_summary(conn, user):
    positions, invested_total, current_value_total = compute_portfolio(conn, user["id"])

    # Ganancias/pérdidas realizadas (de operaciones de venta ya cerradas)
    realized = conn.execute(
        "SELECT COALESCE(SUM(profit_loss), 0) as s FROM transactions "
        "WHERE user_id = ? AND type = 'sell'", (user["id"],)
    ).fetchone()["s"]

    # Capital bloqueado en CDTs activos: sigue siendo patrimonio del
    # usuario, solo que no está disponible como efectivo por ahora.
    cdt_locked = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as s FROM cdts WHERE user_id = ? AND status = 'active'",
        (user["id"],)
    ).fetchone()["s"]
    cdt_interest_earned = conn.execute(
        "SELECT COALESCE(SUM(interest_amount), 0) as s FROM cdt_payments WHERE user_id = ?",
        (user["id"],)
    ).fetchone()["s"]

    unrealized = current_value_total - invested_total
    equity = user["balance"] + current_value_total + cdt_locked
    initial_balance = db.get_initial_balance()
    yield_pct = ((equity - initial_balance) / initial_balance * 100) if initial_balance > 0 else 0.0

    return {
        "positions": positions,
        "available_cash": round(user["balance"], 2),
        "invested": round(invested_total, 2),
        "current_value": round(current_value_total, 2),
        "unrealized_profit": round(unrealized, 2),
        "realized_profit": round(realized, 2),
        "cdt_locked": round(cdt_locked, 2),
        "cdt_interest_earned": round(cdt_interest_earned, 2),
        "equity": round(equity, 2),
        "yield_pct": round(yield_pct, 2),
    }


# --------------------------------------------------------------------------
# API — Autenticación de usuario (solo nombre, sin contraseña)
# --------------------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def api_login():
    """
    Registro / inicio de sesión con usuario, contraseña y curso.
    - Si el nombre no existe: se crea la cuenta automáticamente con la
      contraseña indicada, el curso elegido y el dinero ficticio inicial.
    - Si el nombre ya existe: se valida la contraseña contra el hash
      guardado en la base de datos, y se actualiza el curso guardado
      por si el estudiante cambió de grado.
    La contraseña nunca se guarda ni se envía en texto plano; se guarda
    únicamente su hash (werkzeug.security).
    """
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    course = (data.get("course") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "Escribe un nombre de usuario."}), 400
    if len(name) > 40:
        return jsonify({"ok": False, "error": "El nombre es demasiado largo."}), 400
    if not password:
        return jsonify({"ok": False, "error": "Escribe una contraseña."}), 400
    if len(password) < 4:
        return jsonify({"ok": False, "error": "La contraseña debe tener al menos 4 caracteres."}), 400
    if course not in db.CURSOS_DISPONIBLES:
        return jsonify({"ok": False, "error": "Elige tu curso (8, 9, 10 u 11)."}), 400

    conn = db.get_connection()
    user = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()

    if user is None:
        # Cuenta nueva automática: se crea al vuelo con la contraseña dada
        # y el dinero ficticio inicial definido por el administrador.
        initial = db.get_initial_balance()
        conn.execute(
            "INSERT INTO users (name, password_hash, balance, is_blocked, course, created_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (name, generate_password_hash(password), initial, course, db.now_iso()),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    else:
        if not check_password_hash(user["password_hash"], password):
            conn.close()
            return jsonify({"ok": False, "error": "Contraseña incorrecta."}), 401
        if user["course"] != course:
            conn.execute("UPDATE users SET course = ? WHERE id = ?", (course, user["id"]))
            conn.commit()
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()

    if user["is_blocked"]:
        conn.close()
        return jsonify({"ok": False, "error": "Esta cuenta está bloqueada por el administrador."}), 403

    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    conn.close()

    return jsonify({"ok": True, "user": {"id": user["id"], "name": user["name"], "balance": user["balance"], "course": user["course"]}})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
@login_required
def api_me():
    conn = db.get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if user is None:
        session.clear()
        conn.close()
        return jsonify({"ok": False, "error": "Usuario no encontrado."}), 404
    summary = compute_summary(conn, user)
    conn.close()
    return jsonify({"ok": True, "user": {"id": user["id"], "name": user["name"]}, "summary": summary})


# --------------------------------------------------------------------------
# API — Mercado de acciones
# --------------------------------------------------------------------------
@app.route("/api/stocks")
def api_stocks():
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM stocks WHERE status = 'active' ORDER BY name").fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return jsonify({"ok": True, "stocks": result, "market_running": is_market_running()})


@app.route("/api/stocks/<int:stock_id>/history")
def api_stock_history(stock_id):
    limit = request.args.get("limit", 50, type=int)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT price, timestamp FROM price_history WHERE stock_id = ? "
        "ORDER BY id DESC LIMIT ?", (stock_id, limit)
    ).fetchall()
    conn.close()
    history = [dict(r) for r in reversed(rows)]
    return jsonify({"ok": True, "history": history})


# --------------------------------------------------------------------------
# API — Compra y venta de acciones
# --------------------------------------------------------------------------
@app.route("/api/trade/buy", methods=["POST"])
@login_required
def api_buy():
    data = request.get_json(force=True, silent=True) or {}
    stock_id = data.get("stock_id")
    quantity = data.get("quantity")

    try:
        quantity = round(float(quantity), 6)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Cantidad inválida."}), 400

    if quantity <= 0:
        return jsonify({"ok": False, "error": "La cantidad debe ser mayor que cero."}), 400

    conn = db.get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    stock = get_stock_or_404(conn, stock_id)

    if stock is None or stock["status"] != "active":
        conn.close()
        return jsonify({"ok": False, "error": "La acción no existe o no está disponible."}), 404

    total_cost = round(stock["price"] * quantity, 2)

    if total_cost > user["balance"] + 1e-9:
        conn.close()
        return jsonify({"ok": False, "error": "Saldo insuficiente para esta compra."}), 400

    new_balance = round(user["balance"] - total_cost, 2)
    if new_balance < 0:
        new_balance = 0.0  # nunca permitir saldo negativo

    conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user["id"]))

    # Actualizar o crear la posición del portafolio con precio promedio ponderado
    pos = conn.execute(
        "SELECT * FROM portfolios WHERE user_id = ? AND stock_id = ?",
        (user["id"], stock_id),
    ).fetchone()

    if pos is None:
        conn.execute(
            "INSERT INTO portfolios (user_id, stock_id, quantity, avg_price) VALUES (?, ?, ?, ?)",
            (user["id"], stock_id, quantity, stock["price"]),
        )
    else:
        total_qty = round(pos["quantity"] + quantity, 6)
        new_avg = ((pos["avg_price"] * pos["quantity"]) + (stock["price"] * quantity)) / total_qty
        conn.execute(
            "UPDATE portfolios SET quantity = ?, avg_price = ? WHERE id = ?",
            (total_qty, new_avg, pos["id"]),
        )

    conn.execute(
        "INSERT INTO transactions (user_id, stock_id, type, quantity, price, total, profit_loss, timestamp) "
        "VALUES (?, ?, 'buy', ?, ?, ?, NULL, ?)",
        (user["id"], stock_id, quantity, stock["price"], total_cost, db.now_iso()),
    )
    conn.commit()

    register_trade_volume(stock_id, "buy", total_cost)

    user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    summary = compute_summary(conn, user)
    conn.close()

    return jsonify({"ok": True, "message": f"Compraste {quantity} de {stock['symbol']}.", "summary": summary})


@app.route("/api/trade/sell", methods=["POST"])
@login_required
def api_sell():
    data = request.get_json(force=True, silent=True) or {}
    stock_id = data.get("stock_id")
    quantity = data.get("quantity")

    try:
        quantity = round(float(quantity), 6)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Cantidad inválida."}), 400

    if quantity <= 0:
        return jsonify({"ok": False, "error": "La cantidad debe ser mayor que cero."}), 400

    conn = db.get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    stock = get_stock_or_404(conn, stock_id)

    if stock is None:
        conn.close()
        return jsonify({"ok": False, "error": "La acción no existe."}), 404

    pos = conn.execute(
        "SELECT * FROM portfolios WHERE user_id = ? AND stock_id = ?",
        (user["id"], stock_id),
    ).fetchone()

    if pos is None or pos["quantity"] < quantity - 1e-6:
        conn.close()
        return jsonify({"ok": False, "error": "No tienes suficientes acciones para vender."}), 400

    total_revenue = round(stock["price"] * quantity, 2)
    profit_loss = round((stock["price"] - pos["avg_price"]) * quantity, 2)

    new_balance = round(user["balance"] + total_revenue, 2)
    conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user["id"]))

    remaining_qty = round(pos["quantity"] - quantity, 6)
    if remaining_qty <= 1e-6:
        conn.execute("DELETE FROM portfolios WHERE id = ?", (pos["id"],))
    else:
        conn.execute("UPDATE portfolios SET quantity = ? WHERE id = ?", (remaining_qty, pos["id"]))

    conn.execute(
        "INSERT INTO transactions (user_id, stock_id, type, quantity, price, total, profit_loss, timestamp) "
        "VALUES (?, ?, 'sell', ?, ?, ?, ?, ?)",
        (user["id"], stock_id, quantity, stock["price"], total_revenue, profit_loss, db.now_iso()),
    )
    conn.commit()

    register_trade_volume(stock_id, "sell", total_revenue)

    user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    summary = compute_summary(conn, user)
    conn.close()

    return jsonify({"ok": True, "message": f"Vendiste {quantity} de {stock['symbol']}.", "summary": summary})


# --------------------------------------------------------------------------
# API — Portafolio e historial del usuario
# --------------------------------------------------------------------------
@app.route("/api/portfolio")
@login_required
def api_portfolio():
    conn = db.get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    summary = compute_summary(conn, user)
    conn.close()
    return jsonify({"ok": True, "summary": summary})


@app.route("/api/history")
@login_required
def api_history():
    conn = db.get_connection()
    rows = conn.execute("""
        SELECT t.id, t.type, t.quantity, t.price, t.total, t.profit_loss, t.timestamp,
               s.symbol, s.name
        FROM transactions t
        JOIN stocks s ON s.id = t.stock_id
        WHERE t.user_id = ?
        ORDER BY t.id DESC
    """, (session["user_id"],)).fetchall()
    conn.close()
    return jsonify({"ok": True, "history": [dict(r) for r in rows]})


@app.route("/api/money-history")
@login_required
def api_money_history_full():
    """Historial TOTAL de dinero de la cuenta actual: compras, ventas,
    aperturas y pagos de CDT, ajustes del admin, pagos masivos recibidos y
    depósitos/retiros aprobados en el pasado."""
    rows = db.get_user_ledger(session["user_id"], limit=300)
    return jsonify({"ok": True, "movements": rows})


@app.route("/api/ranking")
def api_ranking():
    conn = db.get_connection()
    users = conn.execute("SELECT * FROM users WHERE is_blocked = 0").fetchall()
    ranking = []
    for u in users:
        _, invested, current_value = compute_portfolio(conn, u["id"])
        equity = u["balance"] + current_value
        realized = conn.execute(
            "SELECT COALESCE(SUM(profit_loss), 0) as s FROM transactions "
            "WHERE user_id = ? AND type = 'sell'", (u["id"],)
        ).fetchone()["s"]
        unrealized = current_value - invested
        ranking.append({
            "name": u["name"],
            "equity": round(equity, 2),
            "gains": round(realized + unrealized, 2),
        })
    conn.close()
    ranking_by_equity = sorted(ranking, key=lambda x: x["equity"], reverse=True)
    ranking_by_gains = sorted(ranking, key=lambda x: x["gains"], reverse=True)
    return jsonify({"ok": True, "by_equity": ranking_by_equity, "by_gains": ranking_by_gains})


@app.route("/api/stats")
@login_required
def api_stats():
    """Estadísticas del usuario: nº operaciones, mejor/peor operación, etc."""
    conn = db.get_connection()
    uid = session["user_id"]
    total_trades = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE user_id = ?", (uid,)
    ).fetchone()["c"]
    buys = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE user_id = ? AND type='buy'", (uid,)
    ).fetchone()["c"]
    sells = conn.execute(
        "SELECT COUNT(*) as c FROM transactions WHERE user_id = ? AND type='sell'", (uid,)
    ).fetchone()["c"]
    best = conn.execute(
        "SELECT s.name, t.profit_loss FROM transactions t JOIN stocks s ON s.id = t.stock_id "
        "WHERE t.user_id = ? AND t.type='sell' ORDER BY t.profit_loss DESC LIMIT 1", (uid,)
    ).fetchone()
    worst = conn.execute(
        "SELECT s.name, t.profit_loss FROM transactions t JOIN stocks s ON s.id = t.stock_id "
        "WHERE t.user_id = ? AND t.type='sell' ORDER BY t.profit_loss ASC LIMIT 1", (uid,)
    ).fetchone()
    conn.close()
    return jsonify({
        "ok": True,
        "total_trades": total_trades,
        "buys": buys,
        "sells": sells,
        "best_trade": dict(best) if best else None,
        "worst_trade": dict(worst) if worst else None,
    })


# --------------------------------------------------------------------------
# La opción para que los usuarios "soliciten dinero" fue retirada: ahora
# el administrador entrega dinero directamente (ficha de usuario, pagos
# masivos manuales o recurrentes). El historial completo de todo el
# dinero que se ha movido en la plataforma está en /admin/api/ledger.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# API — CDT (Certificados de Depósito a Término)
# El usuario elige el monto y a cuántas quincenas lo mete. Cada 15 días
# se le paga el interés (a la tasa vigente al momento de crear el CDT);
# en la última quincena, junto con el interés se devuelve el capital.
# El capital queda bloqueado (descontado del saldo disponible) mientras
# el CDT está activo.
# --------------------------------------------------------------------------
@app.route("/api/cdt/config")
def api_cdt_config():
    rate = float(db.get_config("cdt_rate_percent", "2.0"))
    return jsonify({"ok": True, "rate_percent": rate, "period_days": CDT_PERIOD_DAYS})


@app.route("/api/cdt/create", methods=["POST"])
@login_required
def api_cdt_create():
    data = request.get_json(force=True, silent=True) or {}
    try:
        amount = round(float(data.get("amount", 0)), 2)
        quincenas = int(data.get("quincenas", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Datos inválidos."}), 400

    if amount <= 0:
        return jsonify({"ok": False, "error": "El monto debe ser mayor que cero."}), 400
    if quincenas < 1 or quincenas > 48:
        return jsonify({"ok": False, "error": "Elige entre 1 y 48 quincenas."}), 400

    conn = db.get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()

    if amount > user["balance"] + 1e-9:
        conn.close()
        return jsonify({"ok": False, "error": "Saldo insuficiente para abrir este CDT."}), 400

    rate = float(db.get_config("cdt_rate_percent", "2.0"))
    now = db.now_dt()
    next_payment = now + datetime.timedelta(days=CDT_PERIOD_DAYS)

    new_balance = round(user["balance"] - amount, 2)
    conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user["id"]))
    conn.execute(
        "INSERT INTO cdts (user_id, amount, quincenas_total, quincenas_pagadas, rate_percent, "
        "status, next_payment_at, created_at) VALUES (?, ?, ?, 0, ?, 'active', ?, ?)",
        (user["id"], amount, quincenas, rate, next_payment.strftime("%Y-%m-%d %H:%M:%S"), db.now_iso()),
    )
    conn.commit()

    user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
    summary = compute_summary(conn, user)
    conn.close()

    return jsonify({
        "ok": True,
        "message": f"Abriste un CDT de {amount} a {quincenas} quincenas, con {rate}% de interés por quincena.",
        "summary": summary,
    })


@app.route("/api/cdt/list")
@login_required
def api_cdt_list():
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM cdts WHERE user_id = ? ORDER BY id DESC", (session["user_id"],)
    ).fetchall()
    cdts = []
    for c in rows:
        expected_interest_total = round(c["amount"] * (c["rate_percent"] / 100) * c["quincenas_total"], 2)
        cdts.append({
            "id": c["id"],
            "amount": c["amount"],
            "quincenas_total": c["quincenas_total"],
            "quincenas_pagadas": c["quincenas_pagadas"],
            "rate_percent": c["rate_percent"],
            "status": c["status"],
            "next_payment_at": c["next_payment_at"] if c["status"] == "active" else None,
            "expected_interest_total": expected_interest_total,
            "created_at": c["created_at"],
            "completed_at": c["completed_at"],
        })
    conn.close()
    return jsonify({"ok": True, "cdts": cdts})


@app.route("/api/cdt/payments")
@login_required
def api_cdt_payments():
    conn = db.get_connection()
    rows = conn.execute("""
        SELECT p.*, c.quincenas_total FROM cdt_payments p
        JOIN cdts c ON c.id = p.cdt_id
        WHERE p.user_id = ? ORDER BY p.id DESC
    """, (session["user_id"],)).fetchall()
    conn.close()
    return jsonify({"ok": True, "payments": [dict(r) for r in rows]})


# --------------------------------------------------------------------------
# API — Exportación (PDF / Excel) del historial del usuario
# --------------------------------------------------------------------------
@app.route("/api/export/pdf")
@login_required
def export_pdf():
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    conn = db.get_connection()
    user_name = session.get("user_name", "usuario")
    rows = conn.execute("""
        SELECT t.timestamp, s.name, t.type, t.quantity, t.price, t.profit_loss
        FROM transactions t JOIN stocks s ON s.id = t.stock_id
        WHERE t.user_id = ? ORDER BY t.id DESC
    """, (session["user_id"],)).fetchall()
    conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = [Paragraph(f"Historial de operaciones — {user_name}", styles["Title"]), Spacer(1, 12)]

    data = [["Fecha", "Empresa", "Tipo", "Cantidad", "Precio", "Ganancia/Pérdida"]]
    for r in rows:
        tipo = "Compra" if r["type"] == "buy" else "Venta"
        pl = f"{r['profit_loss']:.2f}" if r["profit_loss"] is not None else "-"
        data.append([r["timestamp"], r["name"], tipo, str(r["quantity"]), f"{r['price']:.2f}", pl])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#132743")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="historial.pdf", mimetype="application/pdf")


@app.route("/api/export/excel")
@login_required
def export_excel():
    from openpyxl import Workbook

    conn = db.get_connection()
    rows = conn.execute("""
        SELECT t.timestamp, s.name, t.type, t.quantity, t.price, t.profit_loss
        FROM transactions t JOIN stocks s ON s.id = t.stock_id
        WHERE t.user_id = ? ORDER BY t.id DESC
    """, (session["user_id"],)).fetchall()
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Historial"
    ws.append(["Fecha", "Empresa", "Tipo", "Cantidad", "Precio", "Ganancia/Pérdida"])
    for r in rows:
        tipo = "Compra" if r["type"] == "buy" else "Venta"
        ws.append([r["timestamp"], r["name"], tipo, r["quantity"], r["price"], r["profit_loss"]])

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(
        buffer, as_attachment=True, download_name="historial.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ==========================================================================
# API DE ADMINISTRACIÓN — protegida por sesión de administrador
# ==========================================================================
@app.route("/admin/api/login", methods=["POST"])
def admin_login():
    data = request.get_json(force=True, silent=True) or {}
    if data.get("username") == ADMIN_USER and data.get("password") == ADMIN_PASS:
        session["is_admin"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Usuario o contraseña incorrectos."}), 401


@app.route("/admin/api/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    return jsonify({"ok": True})


@app.route("/admin/api/session")
def admin_session_check():
    return jsonify({"ok": True, "is_admin": bool(session.get("is_admin"))})


# ---- Gestión de usuarios ----
@app.route("/admin/api/users")
@admin_required
def admin_users():
    conn = db.get_connection()
    users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    result = []
    for u in users:
        _, invested, current_value = compute_portfolio(conn, u["id"])
        result.append({
            "id": u["id"], "name": u["name"], "balance": round(u["balance"], 2),
            "is_blocked": bool(u["is_blocked"]),
            "course": u["course"] or "",
            "invested": round(invested, 2),
            "equity": round(u["balance"] + current_value, 2),
            "created_at": u["created_at"],
        })
    conn.close()
    return jsonify({"ok": True, "users": result})


@app.route("/admin/api/users/create", methods=["POST"])
@admin_required
def admin_create_user():
    import secrets
    import string

    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    balance = data.get("balance", db.get_initial_balance())
    password = (data.get("password") or "").strip()
    course = (data.get("course") or "").strip()

    if not name:
        return jsonify({"ok": False, "error": "El nombre es obligatorio."}), 400
    if course and course not in db.CURSOS_DISPONIBLES:
        return jsonify({"ok": False, "error": "Curso inválido."}), 400
    try:
        balance = max(0.0, float(balance))
    except (TypeError, ValueError):
        balance = db.get_initial_balance()

    generated_password = None
    if not password:
        # Si el administrador no define una contraseña, se genera una
        # aleatoria simple para poder comunicársela al usuario.
        alphabet = string.ascii_uppercase + string.digits
        generated_password = "".join(secrets.choice(alphabet) for _ in range(8))
        password = generated_password
    elif len(password) < 4:
        return jsonify({"ok": False, "error": "La contraseña debe tener al menos 4 caracteres."}), 400

    conn = db.get_connection()
    existing = conn.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchone()
    if existing:
        conn.close()
        return jsonify({"ok": False, "error": "Ya existe un usuario con ese nombre."}), 400
    conn.execute(
        "INSERT INTO users (name, password_hash, balance, is_blocked, course, created_at) VALUES (?, ?, ?, 0, ?, ?)",
        (name, generate_password_hash(password), balance, course, db.now_iso()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "generated_password": generated_password})


@app.route("/admin/api/users/<int:user_id>/update", methods=["POST"])
@admin_required
def admin_update_user(user_id):
    data = request.get_json(force=True, silent=True) or {}
    conn = db.get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        conn.close()
        return jsonify({"ok": False, "error": "Usuario no encontrado."}), 404

    name = data.get("name")
    balance = data.get("balance")
    new_password = (data.get("new_password") or "").strip()
    course = data.get("course")

    if name:
        name = name.strip()
        dup = conn.execute("SELECT id FROM users WHERE name = ? AND id != ?", (name, user_id)).fetchone()
        if dup:
            conn.close()
            return jsonify({"ok": False, "error": "Ese nombre ya está en uso."}), 400
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))

    if balance is not None:
        try:
            balance = max(0.0, float(balance))  # nunca negativo
            conn.execute("UPDATE users SET balance = ? WHERE id = ?", (balance, user_id))
        except (TypeError, ValueError):
            pass

    if course is not None and course != "":
        if course not in db.CURSOS_DISPONIBLES:
            conn.close()
            return jsonify({"ok": False, "error": "Curso inválido."}), 400
        conn.execute("UPDATE users SET course = ? WHERE id = ?", (course, user_id))

    if new_password:
        if len(new_password) < 4:
            conn.close()
            return jsonify({"ok": False, "error": "La nueva contraseña debe tener al menos 4 caracteres."}), 400
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), user_id),
        )

    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    """Elimina una cuenta de forma permanente, junto con todo su historial
    (portafolio, transacciones, CDTs y ajustes de saldo), gracias a que las
    tablas relacionadas tienen ON DELETE CASCADE hacia users."""
    conn = db.get_connection()
    user = conn.execute("SELECT id, name FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        conn.close()
        return jsonify({"ok": False, "error": "Usuario no encontrado."}), 404

    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted_name": user["name"]})


@app.route("/admin/api/users/<int:user_id>/reset", methods=["POST"])
@admin_required
def admin_reset_user(user_id):
    """Reinicia la cuenta: saldo inicial, sin posiciones ni historial."""
    conn = db.get_connection()
    conn.execute("DELETE FROM portfolios WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
    conn.execute(
        "UPDATE users SET balance = ? WHERE id = ?",
        (db.get_initial_balance(), user_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:user_id>/block", methods=["POST"])
@admin_required
def admin_block_user(user_id):
    conn = db.get_connection()
    conn.execute("UPDATE users SET is_blocked = 1 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:user_id>/unblock", methods=["POST"])
@admin_required
def admin_unblock_user(user_id):
    conn = db.get_connection()
    conn.execute("UPDATE users SET is_blocked = 0 WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:user_id>/add-money", methods=["POST"])
@admin_required
def admin_add_money(user_id):
    data = request.get_json(force=True, silent=True) or {}
    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Monto inválido."}), 400
    if amount <= 0:
        return jsonify({"ok": False, "error": "El monto debe ser positivo."}), 400

    conn = db.get_connection()
    conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, user_id))
    conn.execute(
        "INSERT INTO admin_adjustments (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
        (user_id, amount, "Añadido desde el panel admin", db.now_iso()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:user_id>/remove-money", methods=["POST"])
@admin_required
def admin_remove_money(user_id):
    data = request.get_json(force=True, silent=True) or {}
    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Monto inválido."}), 400
    if amount <= 0:
        return jsonify({"ok": False, "error": "El monto debe ser positivo."}), 400

    conn = db.get_connection()
    user = conn.execute("SELECT balance FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        conn.close()
        return jsonify({"ok": False, "error": "Usuario no encontrado."}), 404
    new_balance = max(0.0, user["balance"] - amount)  # nunca negativo
    actually_removed = user["balance"] - new_balance
    conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user_id))
    conn.execute(
        "INSERT INTO admin_adjustments (user_id, amount, reason, created_at) VALUES (?, ?, ?, ?)",
        (user_id, -actually_removed, "Quitado desde el panel admin", db.now_iso()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:user_id>/give-stock", methods=["POST"])
@admin_required
def admin_give_stock(user_id):
    """Regala acciones a un usuario sin cobrarle (ajuste administrativo)."""
    data = request.get_json(force=True, silent=True) or {}
    stock_id = data.get("stock_id")
    try:
        quantity = float(data.get("quantity", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Cantidad inválida."}), 400
    if quantity <= 0:
        return jsonify({"ok": False, "error": "La cantidad debe ser mayor que cero."}), 400

    conn = db.get_connection()
    stock = get_stock_or_404(conn, stock_id)
    if stock is None:
        conn.close()
        return jsonify({"ok": False, "error": "Acción no encontrada."}), 404

    pos = conn.execute(
        "SELECT * FROM portfolios WHERE user_id = ? AND stock_id = ?", (user_id, stock_id)
    ).fetchone()
    if pos is None:
        conn.execute(
            "INSERT INTO portfolios (user_id, stock_id, quantity, avg_price) VALUES (?, ?, ?, ?)",
            (user_id, stock_id, quantity, stock["price"]),
        )
    else:
        total_qty = pos["quantity"] + quantity
        new_avg = ((pos["avg_price"] * pos["quantity"]) + (stock["price"] * quantity)) / total_qty
        conn.execute(
            "UPDATE portfolios SET quantity = ?, avg_price = ? WHERE id = ?",
            (total_qty, new_avg, pos["id"]),
        )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/users/<int:user_id>/take-stock", methods=["POST"])
@admin_required
def admin_take_stock(user_id):
    """Quita acciones a un usuario (ajuste administrativo, sin reembolso)."""
    data = request.get_json(force=True, silent=True) or {}
    stock_id = data.get("stock_id")
    try:
        quantity = float(data.get("quantity", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Cantidad inválida."}), 400
    if quantity <= 0:
        return jsonify({"ok": False, "error": "La cantidad debe ser mayor que cero."}), 400

    conn = db.get_connection()
    pos = conn.execute(
        "SELECT * FROM portfolios WHERE user_id = ? AND stock_id = ?", (user_id, stock_id)
    ).fetchone()
    if pos is None or pos["quantity"] < quantity - 1e-9:
        conn.close()
        return jsonify({"ok": False, "error": "El usuario no tiene suficientes acciones."}), 400

    remaining = pos["quantity"] - quantity
    if remaining <= 1e-9:
        conn.execute("DELETE FROM portfolios WHERE id = ?", (pos["id"],))
    else:
        conn.execute("UPDATE portfolios SET quantity = ? WHERE id = ?", (remaining, pos["id"]))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---- Solicitudes de dinero (depósitos y retiros que los usuarios piden) ----
@app.route("/admin/api/money-requests")
@admin_required
def admin_list_money_requests():
    """Lista todas las solicitudes de depósito/retiro, la más reciente primero."""
    conn = db.get_connection()
    rows = conn.execute("""
        SELECT d.id, d.user_id, d.type, d.amount, d.status, d.note, d.requested_at, d.resolved_at,
               u.name as user_name
        FROM deposit_requests d
        JOIN users u ON u.id = d.user_id
        ORDER BY d.id DESC
    """).fetchall()
    conn.close()
    return jsonify({"ok": True, "requests": [dict(r) for r in rows]})


@app.route("/admin/api/money-requests/<int:request_id>/approve", methods=["POST"])
@admin_required
def admin_approve_money_request(request_id):
    """
    Aprueba la solicitud:
    - Si es 'deposit': agrega el dinero ficticio al saldo del usuario.
    - Si es 'withdraw': resta el dinero, siempre que el saldo actual
      alcance (se revalida aquí por si cambió desde que se pidió).
    """
    conn = db.get_connection()
    dep = conn.execute("SELECT * FROM deposit_requests WHERE id = ?", (request_id,)).fetchone()
    if dep is None:
        conn.close()
        return jsonify({"ok": False, "error": "Solicitud no encontrada."}), 404
    if dep["status"] != "pending":
        conn.close()
        return jsonify({"ok": False, "error": "Esta solicitud ya fue resuelta."}), 400

    if dep["type"] == "withdraw":
        user = conn.execute("SELECT balance FROM users WHERE id = ?", (dep["user_id"],)).fetchone()
        if user is None or user["balance"] + 1e-9 < dep["amount"]:
            conn.close()
            return jsonify({"ok": False, "error": "El usuario ya no tiene saldo suficiente para este retiro."}), 400
        new_balance = max(0.0, round(user["balance"] - dep["amount"], 2))
        conn.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, dep["user_id"]))
    else:
        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (dep["amount"], dep["user_id"]))

    conn.execute(
        "UPDATE deposit_requests SET status = 'approved', resolved_at = ? WHERE id = ?",
        (db.now_iso(), request_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/money-requests/<int:request_id>/reject", methods=["POST"])
@admin_required
def admin_reject_money_request(request_id):
    """Rechaza la solicitud (depósito o retiro): no se modifica el saldo del usuario."""
    conn = db.get_connection()
    dep = conn.execute("SELECT * FROM deposit_requests WHERE id = ?", (request_id,)).fetchone()
    if dep is None:
        conn.close()
        return jsonify({"ok": False, "error": "Solicitud no encontrada."}), 404
    if dep["status"] != "pending":
        conn.close()
        return jsonify({"ok": False, "error": "Esta solicitud ya fue resuelta."}), 400

    conn.execute(
        "UPDATE deposit_requests SET status = 'rejected', resolved_at = ? WHERE id = ?",
        (db.now_iso(), request_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ---- Gestión del mercado ----
@app.route("/admin/api/stocks")
@admin_required
def admin_stocks():
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM stocks ORDER BY name").fetchall()
    conn.close()
    return jsonify({"ok": True, "stocks": [dict(r) for r in rows]})


@app.route("/admin/api/stocks/<int:stock_id>/update", methods=["POST"])
@admin_required
def admin_update_stock(stock_id):
    data = request.get_json(force=True, silent=True) or {}
    conn = db.get_connection()
    stock = get_stock_or_404(conn, stock_id)
    if stock is None:
        conn.close()
        return jsonify({"ok": False, "error": "Acción no encontrada."}), 404

    name = data.get("name")
    price = data.get("price")
    change_percent = data.get("change_percent")
    status = data.get("status")

    if name:
        conn.execute("UPDATE stocks SET name = ? WHERE id = ?", (name.strip(), stock_id))
    if price is not None:
        try:
            price = max(0.01, float(price))
            conn.execute("UPDATE stocks SET price = ? WHERE id = ?", (price, stock_id))
            conn.execute(
                "INSERT INTO price_history (stock_id, price, timestamp) VALUES (?, ?, ?)",
                (stock_id, price, db.now_iso()),
            )
        except (TypeError, ValueError):
            pass
    if change_percent is not None:
        try:
            conn.execute("UPDATE stocks SET change_percent = ? WHERE id = ?", (float(change_percent), stock_id))
        except (TypeError, ValueError):
            pass
    if status in ("active", "inactive"):
        conn.execute("UPDATE stocks SET status = ? WHERE id = ?", (status, stock_id))

    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/admin/api/stocks/<int:stock_id>/adjust", methods=["POST"])
@admin_required
def admin_adjust_stock(stock_id):
    """Botones rápidos y campo manual: sube/baja el precio un % dado."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        percent = float(data.get("percent", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Porcentaje inválido."}), 400

    conn = db.get_connection()
    stock = get_stock_or_404(conn, stock_id)
    if stock is None:
        conn.close()
        return jsonify({"ok": False, "error": "Acción no encontrada."}), 404

    new_price = max(0.01, round(stock["price"] * (1 + percent / 100), 2))
    conn.execute(
        "UPDATE stocks SET price = ?, change_percent = ? WHERE id = ?",
        (new_price, round(percent, 2), stock_id),
    )
    conn.execute(
        "INSERT INTO price_history (stock_id, price, timestamp) VALUES (?, ?, ?)",
        (stock_id, new_price, db.now_iso()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "new_price": new_price})


# ---- Simulación automática del mercado ----
@app.route("/admin/api/market/start", methods=["POST"])
@admin_required
def admin_market_start():
    start_market()
    return jsonify({"ok": True, "running": True})


@app.route("/admin/api/market/stop", methods=["POST"])
@admin_required
def admin_market_stop():
    stop_market()
    return jsonify({"ok": True, "running": False})


@app.route("/admin/api/market/status")
@admin_required
def admin_market_status():
    return jsonify({"ok": True, "running": is_market_running()})


@app.route("/admin/api/market/schedule", methods=["GET", "POST"])
@admin_required
def admin_market_schedule():
    """Configura el horario en que el mercado automático debe encenderse
    y apagarse solo (por ejemplo, de 9 a.m. a 6 p.m., hora de Colombia)."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        enabled = bool(data.get("enabled"))
        try:
            start_hour = int(data.get("start_hour", 9))
            end_hour = int(data.get("end_hour", 18))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Horas inválidas."}), 400

        if not (0 <= start_hour <= 23) or not (0 <= end_hour <= 23):
            return jsonify({"ok": False, "error": "Las horas deben estar entre 0 y 23."}), 400

        db.set_config("market_schedule_enabled", "1" if enabled else "0")
        db.set_config("market_schedule_start_hour", start_hour)
        db.set_config("market_schedule_end_hour", end_hour)
        return jsonify({"ok": True})

    return jsonify({
        "ok": True,
        "enabled": db.get_config("market_schedule_enabled", "0") == "1",
        "start_hour": int(db.get_config("market_schedule_start_hour", "9")),
        "end_hour": int(db.get_config("market_schedule_end_hour", "18")),
        "server_time": db.now_iso(),  # hora de Colombia (America/Bogota)
    })


# ---- CDT: tasa de interés y listado para el administrador ----
@app.route("/admin/api/cdt/rate", methods=["GET", "POST"])
@admin_required
def admin_cdt_rate():
    """El admin consulta o modifica el % de interés que se paga por
    quincena. El cambio solo afecta a los CDTs que se abran de ahí en
    adelante; los ya existentes conservan la tasa con la que se crearon."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        try:
            rate = float(data.get("rate_percent"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Porcentaje inválido."}), 400
        if rate < 0 or rate > 100:
            return jsonify({"ok": False, "error": "El porcentaje debe estar entre 0 y 100."}), 400
        db.set_config("cdt_rate_percent", rate)
        return jsonify({"ok": True})

    return jsonify({"ok": True, "rate_percent": float(db.get_config("cdt_rate_percent", "2.0"))})


@app.route("/admin/api/cdt/list")
@admin_required
def admin_cdt_list():
    """Lista todos los CDTs de todos los usuarios, para supervisión del admin."""
    conn = db.get_connection()
    rows = conn.execute("""
        SELECT c.*, u.name as user_name
        FROM cdts c JOIN users u ON u.id = c.user_id
        ORDER BY c.status = 'active' DESC, c.id DESC
    """).fetchall()
    conn.close()
    return jsonify({"ok": True, "cdts": [dict(r) for r in rows]})


# ---- Pagos masivos del admin a todos los usuarios ----
@app.route("/admin/api/mass-payment/send-now", methods=["POST"])
@admin_required
def admin_mass_payment_send_now():
    """Le envía dinero ficticio a todos los usuarios (o a un curso
    específico) de una sola vez, con un solo comando/botón."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Monto inválido."}), 400
    if amount <= 0:
        return jsonify({"ok": False, "error": "El monto debe ser mayor que cero."}), 400

    target_course = (data.get("target_course") or "").strip()
    if target_course and target_course not in db.CURSOS_DISPONIBLES:
        return jsonify({"ok": False, "error": "Curso inválido."}), 400

    users_count = send_money_to_all_users(amount, source="manual", target_course=target_course)
    if users_count == 0:
        return jsonify({"ok": False, "error": "No hay usuarios en ese grupo todavía."}), 400
    return jsonify({"ok": True, "users_count": users_count})


@app.route("/admin/api/mass-payment/schedule", methods=["GET", "POST"])
@admin_required
def admin_mass_payment_schedule():
    """Configura un pago automático recurrente: cada X días, se le
    acredita un monto fijo a todos los usuarios (o a un curso específico),
    sin intervención manual."""
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        enabled = bool(data.get("enabled"))
        target_course = (data.get("target_course") or "").strip()
        try:
            amount = float(data.get("amount", 0))
            interval_days = float(data.get("interval_days", 1))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Datos inválidos."}), 400
        if enabled and amount <= 0:
            return jsonify({"ok": False, "error": "El monto debe ser mayor que cero."}), 400
        if interval_days <= 0:
            return jsonify({"ok": False, "error": "El intervalo debe ser mayor que cero."}), 400
        if target_course and target_course not in db.CURSOS_DISPONIBLES:
            return jsonify({"ok": False, "error": "Curso inválido."}), 400

        db.set_config("mass_payment_enabled", "1" if enabled else "0")
        db.set_config("mass_payment_amount", amount)
        db.set_config("mass_payment_interval_days", interval_days)
        db.set_config("mass_payment_target_course", target_course)
        return jsonify({"ok": True})

    last_run = db.get_config("mass_payment_last_run", "")
    return jsonify({
        "ok": True,
        "enabled": db.get_config("mass_payment_enabled", "0") == "1",
        "amount": float(db.get_config("mass_payment_amount", "0")),
        "interval_days": float(db.get_config("mass_payment_interval_days", "1")),
        "target_course": db.get_config("mass_payment_target_course", ""),
        "last_run": last_run or None,
    })


@app.route("/admin/api/mass-payment/history")
@admin_required
def admin_mass_payment_history():
    conn = db.get_connection()
    rows = conn.execute("SELECT * FROM mass_payments ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    return jsonify({"ok": True, "payments": [dict(r) for r in rows]})


# --------------------------------------------------------------------------
# Historial de transacciones TOTAL (todo el dinero que se ha movido en la
# plataforma: compras, ventas, pagos de CDT, pagos masivos, ajustes del
# admin y depósitos/retiros aprobados en el pasado).
# --------------------------------------------------------------------------
@app.route("/admin/api/ledger")
@admin_required
def admin_ledger():
    rows = db.get_ledger(limit=500)
    return jsonify({"ok": True, "movements": rows})


# --------------------------------------------------------------------------
# Estadísticas globales (para el panel de administración)
# --------------------------------------------------------------------------
@app.route("/admin/api/stats")
@admin_required
def admin_global_stats():
    conn = db.get_connection()
    total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    total_trades = conn.execute("SELECT COUNT(*) as c FROM transactions").fetchone()["c"]
    total_balance = conn.execute("SELECT COALESCE(SUM(balance),0) as s FROM users").fetchone()["s"]
    active_stocks = conn.execute("SELECT COUNT(*) as c FROM stocks WHERE status='active'").fetchone()["c"]
    pending_money_requests = conn.execute(
        "SELECT COUNT(*) as c FROM deposit_requests WHERE status = 'pending'"
    ).fetchone()["c"]
    conn.close()
    return jsonify({
        "ok": True,
        "total_users": total_users,
        "total_trades": total_trades,
        "total_balance": round(total_balance, 2),
        "active_stocks": active_stocks,
        "pending_money_requests": pending_money_requests,
        "market_running": is_market_running(),
    })


# --------------------------------------------------------------------------
# Inicialización de la base de datos
# --------------------------------------------------------------------------
# Se ejecuta siempre al importar este módulo (no solo con "python app.py"),
# para que también funcione correctamente cuando un servidor de producción
# como gunicorn importa "app:app" directamente.
db.init_db()

# Si el mercado quedó marcado como "corriendo" en un arranque previo, se
# reactiva automáticamente. NOTA: si despliegas con varios workers de
# gunicorn, usa --workers 1 para evitar que el hilo del mercado se
# duplique en cada proceso.
if db.get_config("market_running", "0") == "1":
    start_market()

# El scheduler (CDT, horario del mercado, pagos masivos recurrentes)
# corre siempre, independientemente de si el mercado manual está activo.
threading.Thread(target=scheduler_loop, daemon=True).start()


# --------------------------------------------------------------------------
# Arranque en modo desarrollo local (python app.py)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "1") == "1"
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=debug_mode, host="0.0.0.0", port=port)
