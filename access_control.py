# access_control.py
import time
import logging
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import storage
from config import (
    ADMIN_IDS,
    REQUIRED_CHANNEL,   # hanya untuk ditampilkan di info/paywall (opsional)
    REQUIRED_GROUP,     # hanya untuk ditampilkan di info/paywall (opsional)
    show_menu,          # untuk /start
)

logger = logging.getLogger(__name__)

__all__ = ["ensure_access_start", "ensure_access_feature"]


# ====== Helpers status akses ======
def _now() -> int:
    return int(time.time())

def _is_admin(uid: int) -> bool:
    try:
        return int(uid) in set(ADMIN_IDS or [])
    except Exception:
        return False

def _has_active_pass(u: dict) -> bool:
    """
    Cek masa aktif manual yang di-set owner.
    Mendukung kolom 'paid_until' (baru) dan 'vip_until' (legacy) agar kompatibel.
    """
    try:
        pu = int(u.get("paid_until") or 0)
        vu = int(u.get("vip_until") or 0)
        return max(pu, vu) > _now()
    except Exception:
        return False

def _trial_active(u: dict) -> bool:
    """Anggap storage menyimpan trial_end (epoch detik)."""
    try:
        return int(u.get("trial_end") or 0) > _now()
    except Exception:
        return False


# ====== Paywall manual (EDIT pesan, fallback kirim baru) ======
async def _show_paywall(target):
    """
    target: CallbackQuery atau Message.
    Upaya pertama: EDIT pesan terakhir (menu) → jadi paywall.
    Jika gagal edit, fallback: reply pesan baru.
    """
    ch = escape(str(REQUIRED_CHANNEL)) if REQUIRED_CHANNEL else "-"
    gr = escape(str(REQUIRED_GROUP)) if REQUIRED_GROUP else "-"

    text = (
        "🔒 *Akses diperlukan*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ *Trial kamu sudah habis.*\n"
        "👤 *Untuk melanjutkan, hubungi owner:* @pudidi\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ *Syarat komunitas*\n"
        f"• Channel: {ch}\n"
        f"• Group: {gr}\n"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Owner", url="https://t.me/pudidi")],
        [InlineKeyboardButton("🔁 Cek Lagi", callback_data="ac_check")],
        [InlineKeyboardButton("🏠 Home", callback_data="nav_home")]
    ])

    try:
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
            return
        if hasattr(target, "edit_text"):
            await target.edit_text(text, reply_markup=kb, parse_mode="Markdown")
            return
    except Exception as e:
        logger.debug(f"paywall edit failed → fallback send: {e}")

    try:
        if hasattr(target, "message"):  # CallbackQuery
            await target.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")
        else:                           # Message
            await target.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"paywall send failed: {e}")


# ====== Public API dipanggil dari main.py ======
async def ensure_access_start(update, context) -> bool:
    """
    Dipanggil saat /start atau tombol 'Cek Lagi'.
    Sesuai permintaan: /start TIDAK memblokir — langsung tampil menu utama.
    """
    try:
        context.user_data.clear()
        msg = getattr(update, "message", None)
        if msg:
            await show_menu(msg, "main")
        else:
            q = getattr(update, "callback_query", None)
            if q:
                await show_menu(q, "main", edit=True)
        return True
    except Exception as e:
        logger.error(f"ensure_access_start error: {e}")
        return False


async def ensure_access_feature(update, context) -> bool:
    """
    Dipanggil SETIAP KALI user menekan tombol fitur / kirim input untuk fitur.
    Jika akses belum valid → EDIT pesan menu jadi paywall manual.
    Return:
      True  → lanjutkan fitur
      False → sudah ditahan & paywall ditampilkan
    """
    try:
        user = None
        msg_target = None

        q = getattr(update, "callback_query", None)
        if q:
            user = q.from_user
            msg_target = q

        m = getattr(update, "message", None)
        if (user is None) and m:
            user = m.from_user
            msg_target = m

        if not user:
            return True

        uid = user.id
        if _is_admin(uid):
            return True

        # Ambil profil user dari storage
        u = storage.get_or_create_user(uid)
        if _has_active_pass(u) or _trial_active(u):
            return True

        # Tidak punya akses → tampilkan paywall manual
        await _show_paywall(msg_target)
        return False

    except Exception as e:
        logger.error(f"ensure_access_feature error: {e}")
        # Untuk keamanan UX: jangan memblokir saat terjadi error
        return True
