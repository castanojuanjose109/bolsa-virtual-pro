# -*- coding: utf-8 -*-
"""
database.py
------------
Capa de acceso a datos de Bolsa Virtual Pro.

Usa SQLite por simplicidad, pero todas las consultas están escritas con
SQL estándar (parámetros con "?") para que sea sencillo migrar a MySQL
cambiando únicamente la función get_connection() y algunos tipos de datos.

Todas las funciones abren y cierran su propia conexión, lo que evita
problemas de concurrencia con el servidor de desarrollo de Flask
(que puede atender varias peticiones en hilos distintos).
"""

import sqlite3
import os
import datetime

try:
    from zoneinfo import ZoneInfo
    COLOMBIA_TZ = ZoneInfo("America/Bogota")
except Exception:
    COLOMBIA_TZ = None

# Ruta absoluta del archivo de base de datos
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bolsa.db")

# Dinero inicial que recibe cada cuenta nueva.
# Se deja en 0: los usuarios deben solicitar depósitos, que el
# administrador aprueba o rechaza desde el panel /admin.
INITIAL_BALANCE = 0.0

# Cursos disponibles para las cuentas de los estudiantes.
CURSOS_DISPONIBLES = ["8", "9", "10", "11"]

# Empresas ficticias que forman el mercado por defecto.
# Precios pensados para ser accesibles (permiten comprar varias acciones
# completas con montos pequeños) y para que las compras fraccionarias
# (0.1, 0.5, etc.) tengan sentido en la práctica.
DEFAULT_STOCKS = [
    # symbol, name, price
    ("AAPL", "Apple",       19.05),
    ("MSFT", "Microsoft",   41.52),
    ("TSLA", "Tesla",       24.88),
    ("AMZN", "Amazon",      17.83),
    ("GOOGL", "Google",     16.59),
    ("NVDA", "Nvidia",      13.14),
    ("META", "Meta",        50.21),
    ("KO",   "Coca-Cola",    6.22),
    ("NFLX", "Netflix",     64.18),
    ("SSNL", "Samsung",      7.76),
]


def get_connection():
    """Devuelve una nueva conexión a la base de datos con row_factory tipo dict."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_dt():
    """Fecha/hora actual en zona horaria de Colombia (America/Bogota),
    sin importar en qué país/servidor esté corriendo la aplicación.
    Si por alguna razón el sistema no tiene datos de zona horaria
    disponibles (poco común), cae de vuelta a la hora local del equipo."""
    if COLOMBIA_TZ is not None:
        return datetime.datetime.now(COLOMBIA_TZ).replace(tzinfo=None)
    return datetime.datetime.now()


def now_iso():
    """Marca de tiempo estándar (hora de Colombia) usada en toda la app."""
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    """
    Crea todas las tablas si no existen y siembra datos iniciales
    (acciones por defecto y configuración global).
    Es seguro llamarla en cada arranque de la aplicación.
    """
    conn = get_connection()
    cur = conn.cursor()

    # --- Tabla de usuarios ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            balance REAL NOT NULL DEFAULT 0,
            is_blocked INTEGER NOT NULL DEFAULT 0,
            course TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    # --- Tabla de acciones (mercado) ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            change_percent REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active'
        )
    """)

    # --- Historial de precios (para gráficos) ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (stock_id) REFERENCES stocks (id) ON DELETE CASCADE
        )
    """)

    # --- Portafolios (posiciones abiertas de cada usuario) ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stock_id INTEGER NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            avg_price REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, stock_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (stock_id) REFERENCES stocks (id) ON DELETE CASCADE
        )
    """)

    # --- Historial de operaciones (compras / ventas) ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            stock_id INTEGER NOT NULL,
            type TEXT NOT NULL,               -- 'buy' o 'sell'
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            total REAL NOT NULL,
            profit_loss REAL,                 -- solo aplica a ventas
            timestamp TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (stock_id) REFERENCES stocks (id) ON DELETE CASCADE
        )
    """)

    # --- Solicitudes de dinero ficticio: depósito o retiro (requieren aprobación admin) ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS deposit_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL DEFAULT 'deposit',      -- 'deposit' o 'withdraw'
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | rejected
            note TEXT,
            requested_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    # --- CDTs: certificados de depósito a término (el usuario elige a
    # cuántas quincenas los mete; pagan intereses cada 15 días) ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cdts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,             -- capital invertido (queda bloqueado)
            quincenas_total INTEGER NOT NULL, -- a cuántas quincenas lo metió el usuario
            quincenas_pagadas INTEGER NOT NULL DEFAULT 0,
            rate_percent REAL NOT NULL,       -- % de interés por quincena, fijado al crear
            status TEXT NOT NULL DEFAULT 'active',  -- active | completed
            next_payment_at TEXT NOT NULL,    -- fecha/hora del próximo pago quincenal
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    # --- Historial de pagos de intereses de CDT ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cdt_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cdt_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            quincena_numero INTEGER NOT NULL,
            interest_amount REAL NOT NULL,
            principal_returned REAL NOT NULL DEFAULT 0,  -- > 0 solo en el último pago
            paid_at TEXT NOT NULL,
            FOREIGN KEY (cdt_id) REFERENCES cdts (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    # --- Historial de pagos masivos del admin a todos los usuarios ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mass_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            users_count INTEGER NOT NULL,
            source TEXT NOT NULL,   -- 'manual' | 'recurring'
            target_course TEXT NOT NULL DEFAULT '',  -- '' = todos los cursos
            paid_at TEXT NOT NULL
        )
    """)

    # --- Quién recibió exactamente cada pago masivo (para el historial
    # personal de cada cuenta, sin importar si luego cambia de curso). ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mass_payment_recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mass_payment_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            paid_at TEXT NOT NULL,
            FOREIGN KEY (mass_payment_id) REFERENCES mass_payments (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    # --- Ajustes manuales de saldo hechos por el admin (+/-) desde la
    # ficha de un usuario; quedan registrados para el historial total. ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,      -- positivo = se añadió, negativo = se quitó
            reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    # --- Configuración global de la aplicación ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    conn.commit()

    # --- Migraciones suaves para bases de datos creadas con una versión
    # anterior del esquema (agregan columnas nuevas si todavía no existen,
    # sin borrar ningún dato). Es seguro correr esto en cada arranque. ---
    def _column_exists(table, column):
        cols = [r["name"] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
        return column in cols

    if not _column_exists("users", "course"):
        cur.execute("ALTER TABLE users ADD COLUMN course TEXT NOT NULL DEFAULT ''")
    if not _column_exists("mass_payments", "target_course"):
        cur.execute("ALTER TABLE mass_payments ADD COLUMN target_course TEXT NOT NULL DEFAULT ''")
    conn.commit()

    # Sembrar acciones por defecto si la tabla está vacía
    cur.execute("SELECT COUNT(*) as c FROM stocks")
    if cur.fetchone()["c"] == 0:
        for symbol, name, price in DEFAULT_STOCKS:
            cur.execute(
                "INSERT INTO stocks (symbol, name, price, change_percent, status) "
                "VALUES (?, ?, ?, 0, 'active')",
                (symbol, name, price),
            )
        conn.commit()
        # Registrar el primer punto del historial de precios
        cur.execute("SELECT id, price FROM stocks")
        ts = now_iso()
        for row in cur.fetchall():
            cur.execute(
                "INSERT INTO price_history (stock_id, price, timestamp) VALUES (?, ?, ?)",
                (row["id"], row["price"], ts),
            )
        conn.commit()

    # Sembrar configuración por defecto
    defaults = {
        "market_running": "0",
        "initial_balance": str(INITIAL_BALANCE),
        # CDT: % de interés que se paga cada quincena (15 días)
        "cdt_rate_percent": "2.0",
        # Horario programado del mercado automático (hora de Colombia, 0-23)
        "market_schedule_enabled": "0",
        "market_schedule_start_hour": "9",
        "market_schedule_end_hour": "18",
        # Pago recurrente del admin a todos los usuarios (o a un curso)
        "mass_payment_enabled": "0",
        "mass_payment_amount": "0",
        "mass_payment_interval_days": "1",
        "mass_payment_target_course": "",
        "mass_payment_last_run": "",
    }
    for key, value in defaults.items():
        cur.execute("SELECT value FROM config WHERE key = ?", (key,))
        if cur.fetchone() is None:
            cur.execute("INSERT INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

    conn.close()


def get_config(key, default=None):
    conn = get_connection()
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_config(key, value):
    conn = get_connection()
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_initial_balance():
    return float(get_config("initial_balance", INITIAL_BALANCE))


def get_user_ids_by_course(course):
    """Devuelve los ids de usuario que pertenecen a un curso dado.
    Si course es '' o None, devuelve TODOS los usuarios."""
    conn = get_connection()
    if course:
        rows = conn.execute("SELECT id FROM users WHERE course = ?", (course,)).fetchall()
    else:
        rows = conn.execute("SELECT id FROM users").fetchall()
    conn.close()
    return [r["id"] for r in rows]


def get_user_ledger(user_id, limit=300):
    """Historial de transacciones de dinero de UNA cuenta: compras, ventas,
    apertura y pagos de CDT, ajustes del admin, pagos masivos recibidos y
    depósitos/retiros aprobados en el pasado. Todo en un solo listado
    ordenado del más reciente al más antiguo."""
    conn = get_connection()

    rows = conn.execute("""
        SELECT 'Compra' as kind, -t.total as amount,
               (printf('%.4f', t.quantity) || ' ' || s.symbol || ' @ ' || printf('%.2f', t.price)) as detail,
               t.timestamp as ts
        FROM transactions t
        JOIN stocks s ON s.id = t.stock_id
        WHERE t.user_id = ? AND t.type = 'buy'

        UNION ALL

        SELECT 'Venta', t.total,
               (printf('%.4f', t.quantity) || ' ' || s.symbol || ' @ ' || printf('%.2f', t.price)),
               t.timestamp
        FROM transactions t
        JOIN stocks s ON s.id = t.stock_id
        WHERE t.user_id = ? AND t.type = 'sell'

        UNION ALL

        SELECT 'CDT — apertura (pasa a pasivos)', -c.amount,
               ('A ' || c.quincenas_total || ' quincenas · ' || c.rate_percent || '% quincenal'),
               c.created_at
        FROM cdts c
        WHERE c.user_id = ?

        UNION ALL

        SELECT 'CDT — pago de quincena', (p.interest_amount + p.principal_returned),
               ('Quincena ' || p.quincena_numero || CASE WHEN p.principal_returned > 0 THEN ' (incluye capital, vuelve a activos)' ELSE '' END),
               p.paid_at
        FROM cdt_payments p
        WHERE p.user_id = ?

        UNION ALL

        SELECT CASE WHEN a.amount >= 0 THEN 'Ajuste del admin (+)' ELSE 'Ajuste del admin (-)' END,
               a.amount, COALESCE(NULLIF(a.reason, ''), 'Sin motivo'), a.created_at
        FROM admin_adjustments a
        WHERE a.user_id = ?

        UNION ALL

        SELECT CASE WHEN mp.source = 'manual' THEN 'Pago masivo' ELSE 'Pago masivo recurrente' END,
               r.amount,
               ('Grupo: ' || CASE WHEN mp.target_course = '' THEN 'todos' ELSE ('curso ' || mp.target_course) END),
               r.paid_at
        FROM mass_payment_recipients r
        JOIN mass_payments mp ON mp.id = r.mass_payment_id
        WHERE r.user_id = ?

        UNION ALL

        SELECT CASE WHEN d.type = 'deposit' THEN 'Depósito aprobado' ELSE 'Retiro aprobado' END,
               CASE WHEN d.type = 'deposit' THEN d.amount ELSE -d.amount END,
               COALESCE(NULLIF(d.note, ''), '—'), d.resolved_at
        FROM deposit_requests d
        WHERE d.user_id = ? AND d.status = 'approved'

        ORDER BY ts DESC
        LIMIT ?
    """, (user_id, user_id, user_id, user_id, user_id, user_id, user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ledger(limit=500):
    """Historial TOTAL de movimientos de dinero de toda la plataforma:
    compras, ventas, pagos de CDT, pagos masivos, ajustes manuales del
    admin y depósitos/retiros aprobados en el pasado. Todo en un solo
    listado ordenado del más reciente al más antiguo."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT u.name as user_name, 'Compra' as kind, -t.total as amount,
               (printf('%.4f', t.quantity) || ' ' || s.symbol || ' @ ' || printf('%.2f', t.price)) as detail,
               t.timestamp as ts
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        JOIN stocks s ON s.id = t.stock_id
        WHERE t.type = 'buy'

        UNION ALL

        SELECT u.name, 'Venta', t.total,
               (printf('%.4f', t.quantity) || ' ' || s.symbol || ' @ ' || printf('%.2f', t.price)),
               t.timestamp
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        JOIN stocks s ON s.id = t.stock_id
        WHERE t.type = 'sell'

        UNION ALL

        SELECT u.name, 'Pago de CDT', (p.interest_amount + p.principal_returned),
               ('Quincena ' || p.quincena_numero || CASE WHEN p.principal_returned > 0 THEN ' (incluye capital)' ELSE '' END),
               p.paid_at
        FROM cdt_payments p
        JOIN users u ON u.id = p.user_id

        UNION ALL

        SELECT ('Todos los usuarios (' || m.users_count || ')'),
               CASE WHEN m.source = 'manual' THEN 'Pago masivo' ELSE 'Pago masivo recurrente' END,
               m.amount,
               ('Grupo: ' || CASE WHEN m.target_course = '' THEN 'todos' ELSE ('curso ' || m.target_course) END),
               m.paid_at
        FROM mass_payments m

        UNION ALL

        SELECT u.name, CASE WHEN a.amount >= 0 THEN 'Ajuste admin (+)' ELSE 'Ajuste admin (-)' END,
               a.amount, COALESCE(NULLIF(a.reason, ''), 'Sin motivo'), a.created_at
        FROM admin_adjustments a
        JOIN users u ON u.id = a.user_id

        UNION ALL

        SELECT u.name, CASE WHEN d.type = 'deposit' THEN 'Depósito aprobado' ELSE 'Retiro aprobado' END,
               CASE WHEN d.type = 'deposit' THEN d.amount ELSE -d.amount END,
               COALESCE(NULLIF(d.note, ''), '—'), d.resolved_at
        FROM deposit_requests d
        JOIN users u ON u.id = d.user_id
        WHERE d.status = 'approved'

        ORDER BY ts DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
