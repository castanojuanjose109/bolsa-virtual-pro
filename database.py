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

# Ruta absoluta del archivo de base de datos
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bolsa.db")

# Dinero inicial que recibe cada cuenta nueva.
# Se deja en 0: los usuarios deben solicitar depósitos, que el
# administrador aprueba o rechaza desde el panel /admin.
INITIAL_BALANCE = 0.0

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


def now_iso():
    """Marca de tiempo estándar ISO 8601 usada en toda la app."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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

    # --- Configuración global de la aplicación ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

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
