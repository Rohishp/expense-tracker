"""SQLite data layer for Spendly.

No ORM — raw sqlite3 with parameterized queries only.
"""

import calendar
import os
import sqlite3
from datetime import date

from werkzeug.security import generate_password_hash

# database/db.py -> database/ -> project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "expense_tracker.db")

CATEGORIES = [
    "Food", "Transport", "Bills", "Health",
    "Entertainment", "Shopping", "Other",
]


def get_db():
    """Open a new SQLite connection with row access and FK enforcement.

    PRAGMA foreign_keys is per-connection in SQLite, so it must be
    reissued on every call — it is never persisted.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if they don't already exist. Safe to call repeatedly."""
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                date TEXT NOT NULL,
                description TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incomes (
                user_id INTEGER PRIMARY KEY,
                monthly_amount REAL NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS obligations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _spread_dates(count):
    """Return `count` YYYY-MM-DD strings spread across the current month."""
    today = date.today()
    _, days_in_month = calendar.monthrange(today.year, today.month)
    step = max(1, days_in_month // count)
    days = [min(1 + i * step, days_in_month) for i in range(count)]
    return [date(today.year, today.month, d).isoformat() for d in days]


def seed_db():
    """Insert one demo user and 8 sample expenses, once only."""
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        if row["count"] > 0:
            return

        password_hash = generate_password_hash("demo123")
        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            ("Demo User", "demo@spendly.com", password_hash),
        )
        user_id = cursor.lastrowid

        dates = _spread_dates(8)
        sample_expenses = [
            (user_id, 12.50, "Food", dates[0], "Groceries at the supermarket"),
            (user_id, 8.00, "Transport", dates[1], "Bus fare"),
            (user_id, 65.00, "Bills", dates[2], "Electricity bill"),
            (user_id, 30.00, "Health", dates[3], "Pharmacy purchase"),
            (user_id, 15.75, "Entertainment", dates[4], "Movie tickets"),
            (user_id, 42.20, "Shopping", dates[5], "New shoes"),
            (user_id, 9.99, "Other", dates[6], "Miscellaneous purchase"),
            (user_id, 22.30, "Food", dates[7], "Dinner with friends"),
        ]
        conn.executemany(
            """
            INSERT INTO expenses (user_id, amount, category, date, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            sample_expenses,
        )
        conn.commit()
    finally:
        conn.close()
