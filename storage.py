# storage.py
import sqlite3
import os
import logging
from typing import Optional, List, Tuple

DB_PATH = os.getenv("DB_PATH", "users.db")
logger = logging.getLogger(__name__)

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            plan        TEXT CHECK(plan IN ('monthly','permanent')) NOT NULL,
            expires_at  INTEGER NULL
        )
        """)
        c.commit()

def get_username(user_id: int) -> Optional[str]:
    # fungsi placeholder. Kamu bisa isi dari user cache sendiri kalau ada.
    # Default: None, supaya di list muncul '-'.
    return None

def add_or_update_subscription(user_id: int, username: Optional[str], plan: str, expires_at: Optional[int]):
    init_db()
    with _conn() as c:
        c.execute("""
        INSERT INTO subscriptions (user_id, username, plan, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          username=excluded.username,
          plan=excluded.plan,
          expires_at=excluded.expires_at
        """, (user_id, username, plan, expires_at))
        c.commit()

def get_all_subscribers() -> List[Tuple[int, Optional[str], str, Optional[int]]]:
    init_db()
    with _conn() as c:
        cur = c.execute("SELECT user_id, username, plan, expires_at FROM subscriptions ORDER BY user_id ASC")
        rows = [(r["user_id"], r["username"], r["plan"], r["expires_at"]) for r in cur.fetchall()]
        return rows

def delete_user(user_id: int) -> None:
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        c.commit()

def is_active(user_id: int) -> bool:
    """Berguna bila kamu mau cek akses fitur di tempat lain."""
    init_db()
    with _conn() as c:
        cur = c.execute("SELECT plan, expires_at FROM subscriptions WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            return False
        if row["plan"] == "permanent":
            return True
        # monthly: valid jika now <= expires_at
        import time
        return row["expires_at"] is not None and time.time() <= row["expires_at"]
