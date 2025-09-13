# storage.py
import sqlite3
import os
import logging
import time
from typing import Optional, List, Tuple, Dict
from config import TRIAL_MINUTES, OWNER_IDS

DB_PATH = os.getenv("DB_PATH", "users.db")
logger = logging.getLogger(__name__)

# Normalisasi nama paket
PLAN_MAP = {
    "monthly": "1bulan",
    "1bulan": "1bulan",
    "1hari": "1hari",
    "1minggu": "1minggu",
    "permanent": "permanent",
}

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Buat tabel jika belum ada."""
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id     INTEGER PRIMARY KEY,
            name        TEXT,
            plan        TEXT CHECK(plan IN ('1hari','1minggu','1bulan','permanent')) NOT NULL,
            expires_at  INTEGER NULL
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            trial_end   INTEGER DEFAULT 0,
            paid_until  INTEGER DEFAULT 0
        )
        """)
        c.commit()

# ==========================================
# User / Trial table
# ==========================================
def get_or_create_user(user_id: int) -> Dict:
    """Ambil user dari tabel users. Kalau belum ada → buat dengan trial."""
    init_db()
    with _conn() as c:
        cur = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row:
            return dict(row)

        trial_end = int(time.time()) + TRIAL_MINUTES * 60
        c.execute(
            "INSERT INTO users (user_id, trial_end, paid_until) VALUES (?, ?, ?)",
            (user_id, trial_end, 0)
        )
        c.commit()
        return {"user_id": user_id, "trial_end": trial_end, "paid_until": 0}

def get_user(user_id: int) -> Optional[Dict]:
    """Ambil user dari tabel users."""
    init_db()
    with _conn() as c:
        cur = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def get_all_users() -> List[Dict]:
    """Ambil semua user dari tabel users."""
    init_db()
    with _conn() as c:
        cur = c.execute("SELECT * FROM users ORDER BY user_id ASC")
        return [dict(r) for r in cur.fetchall()]

# ==========================================
# Subscription table
# ==========================================
def add_or_update_subscription(user_id: int, name: Optional[str], plan: str, expires_at: Optional[int]):
    """Tambah / update subscription user."""
    init_db()
    plan = PLAN_MAP.get(plan, plan)
    with _conn() as c:
        c.execute("""
        INSERT INTO subscriptions (user_id, name, plan, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          name=excluded.name,
          plan=excluded.plan,
          expires_at=excluded.expires_at
        """, (user_id, name, plan, expires_at))
        c.commit()

def get_subscription(user_id: int) -> Optional[Dict]:
    """Ambil subscription user dari tabel subscriptions."""
    init_db()
    with _conn() as c:
        cur = c.execute("SELECT user_id, name, plan, expires_at FROM subscriptions WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def get_all_subscribers() -> List[Tuple[int, Optional[str], str, Optional[int]]]:
    """Ambil semua subscription."""
    init_db()
    with _conn() as c:
        cur = c.execute("SELECT user_id, name, plan, expires_at FROM subscriptions ORDER BY user_id ASC")
        return [(r["user_id"], r["name"], r["plan"], r["expires_at"]) for r in cur.fetchall()]

def delete_user(user_id: int) -> None:
    """Hapus subscription user (tidak hapus trial)."""
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))
        c.commit()

# ==========================================
# Status & helpers
# ==========================================
def get_user_status(user_id: int) -> Dict:
    """Ambil status user (owner / permanent / plan aktif / trial / expired)."""
    now = int(time.time())

    # owner
    if user_id in OWNER_IDS:
        return {"type": "owner", "expires_at": None, "left_seconds": 0}

    # cek subscription
    sub = get_subscription(user_id)
    if sub:
        plan = PLAN_MAP.get(sub.get("plan"), sub.get("plan"))
        exp = sub.get("expires_at") or 0

        if plan == "permanent":
            return {"type": "permanent", "expires_at": None, "left_seconds": 0}

        if plan in ("1hari", "1minggu", "1bulan"):
            left = max(0, exp - now)
            return {
                "type": plan if left > 0 else "expired",
                "expires_at": exp,
                "left_seconds": left
            }

    # fallback trial
    u = get_or_create_user(user_id)
    trial_end = int(u.get("trial_end") or 0)
    if trial_end > now:
        return {"type": "trial", "expires_at": trial_end, "left_seconds": trial_end - now}

    return {"type": "expired", "expires_at": None, "left_seconds": 0}

def get_active_subscribers() -> List[Dict]:
    """Ambil semua user dengan plan aktif atau permanent."""
    now = int(time.time())
    rows = get_all_subscribers()
    active = []
    for uid, name, plan, exp in rows:
        if plan == "permanent":
            active.append({"user_id": uid, "name": name, "plan": plan, "expires_at": None})
        elif plan in ("1hari", "1minggu", "1bulan") and exp and exp > now:
            active.append({"user_id": uid, "name": name, "plan": plan, "expires_at": exp})
    return active

def get_expired_subscribers() -> List[Dict]:
    """Ambil semua user expired (bukan permanent)."""
    now = int(time.time())
    rows = get_all_subscribers()
    expired = []
    for uid, name, plan, exp in rows:
        if plan != "permanent" and (not exp or exp <= now):
            expired.append({"user_id": uid, "name": name, "plan": plan, "expires_at": exp})
    return expired

def get_user_detail(user_id: int) -> Dict:
    """Ambil detail user (gabungan subscription + status)."""
    status = get_user_status(user_id)
    sub = get_subscription(user_id)
    return {
        "user_id": user_id,
        "name": sub.get("name") if sub else None,
        "plan": sub.get("plan") if sub else None,
        **status
    }

def cleanup_expired() -> int:
    """Hapus semua subscription expired. Return jumlah yang dihapus."""
    now = int(time.time())
    init_db()
    with _conn() as c:
        cur = c.execute("DELETE FROM subscriptions WHERE plan != 'permanent' AND (expires_at IS NULL OR expires_at <= ?)", (now,))
        deleted = cur.rowcount
        c.commit()
    return deleted
