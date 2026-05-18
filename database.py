"""
Database operations — SQLite schema + CRUD helpers.
"""

import sqlite3
import json
from datetime import datetime
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,          -- BUY or SELL
                entry_price REAL NOT NULL,
                quantity REAL NOT NULL,
                exit_price REAL,             -- NULL until position closed
                pnl REAL,                     -- realized PnL
                pnl_pct REAL,
                strategy TEXT DEFAULT 'RSI',
                notes TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS equity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    equity REAL NOT NULL,
                    cash REAL NOT NULL,
                    position_value REAL NOT NULL,
                    price REAL NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS config_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event TEXT NOT NULL,
                    detail TEXT
            )
        """)
        conn.commit()
        # seed initial balance
        from config import INITIAL_CAPITAL
        c.execute(
            "INSERT OR IGNORE INTO equity_log (timestamp, equity, cash, position_value, price) "
            "VALUES (?, ?, ?, 0, 0)",
            (datetime.now().isoformat(), INITIAL_CAPITAL, INITIAL_CAPITAL),
        )
        conn.commit()


# ─── Trade CRUD ──────────────────────────────────────────────────────────────

def insert_trade(symbol: str, side: str, price: float, qty: float, notes=""):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO trades (timestamp, symbol, side, entry_price, quantity, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), symbol, side, price, qty, notes))
        conn.commit()
        return c.lastrowid


def close_trade(trade_id: int, exit_price: float, pnl: float, pnl_pct: float):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            UPDATE trades
            SET exit_price=?, pnl=?, pnl_pct=?
            WHERE id=? AND exit_price IS NULL
        """, (exit_price, pnl, pnl_pct, trade_id))
        conn.commit()


def get_last_open_trade(symbol: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT * FROM trades
            WHERE symbol=? AND exit_price IS NULL
            ORDER BY id DESC LIMIT 1
        """, (symbol,))
        row = c.fetchone()
        return dict(row) if row else None


def get_all_trades(limit=100):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT * FROM trades ORDER BY id DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in c.fetchall()]


def get_closed_trades_count():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades WHERE exit_price IS NOT NULL")
        return c.fetchone()[0]


# ─── Equity Log ─────────────────────────────────────────────────────────────

def log_equity(equity: float, cash: float, position_value: float, price: float):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO equity_log (timestamp, equity, cash, position_value, price)
            VALUES (?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(), equity, cash, position_value, price))
        conn.commit()


def get_equity_history(limit=500):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT timestamp, equity FROM equity_log
            ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = c.fetchall()
        rows.reverse()
        return [dict(r) for r in rows]


def get_latest_equity():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM equity_log ORDER BY id DESC LIMIT 1"
        )
        row = c.fetchone()
        return dict(row) if row else None


# ─── Config Log ──────────────────────────────────────────────────────────────

def log_event(event: str, detail=""):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO config_log (timestamp, event, detail)
            VALUES (?, ?, ?)
        """, (datetime.now().isoformat(), event, detail))
        conn.commit()


# ─── Stats ───────────────────────────────────────────────────────────────────

def get_stats():
    """Return a dict of key trading stats."""
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades WHERE exit_price IS NOT NULL")
        total_trades = c.fetchone()[0]
        c.execute("SELECT SUM(pnl) FROM trades WHERE exit_price IS NOT NULL")
        total_pnl = c.fetchone()[0] or 0.0
        c.execute("SELECT AVG(pnl) FROM trades WHERE exit_price IS NOT NULL")
        avg_pnl = c.fetchone()[0] or 0.0
        c.execute(
            "SELECT SUM(pnl) FROM trades WHERE pnl > 0 AND exit_price IS NOT NULL"
        )
        winning_pnl = c.fetchone()[0] or 0.0
        c.execute(
            "SELECT COUNT(*) FROM trades WHERE pnl > 0 AND exit_price IS NOT NULL"
        )
        winning_trades = c.fetchone()[0]
        c.execute(
            "SELECT COUNT(*) FROM trades WHERE exit_price IS NOT NULL"
        )
        closed_trades = c.fetchone()[0]
        win_rate = round(winning_trades / closed_trades * 100, 2) if closed_trades else 0.0
        return {
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "winning_trades": winning_trades,
            "winning_pnl": round(winning_pnl, 2),
            "win_rate": win_rate,
        }
