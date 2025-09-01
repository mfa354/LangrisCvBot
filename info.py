import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from html import escape

import storage
from config import REQUIRED_CHANNEL, REQUIRED_GROUP, TRIAL_MINUTES, OWNER_ID

logger = logging.getLogger(__name__)

__all__ = ["InfoHandler"]

def _now_ts() -> int:
    from time import time
    return int(time())

def _human_left(seconds: int) -> str:
    """Ubah detik → format jam/menit yang mudah dibaca"""
    if seconds <= 0:
        return "0m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h and m:
        return f"{h}j {m}m"
    if h:
        return f"{h}j"
    return f"{m}m"

def _fmt_info_text(user_id: int) -> str:
    ch = escape(REQUIRED_CHANNEL or "-")
    gr = escape(REQUIRED_GROUP or "-")

    status = storage.get_user_status(user_id)
    now = _now_ts()

    # === Mapping tipe akses ===
    if status["type"] == "owner":
        return (
            "👑 <b>INFO OWNER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Halo King! Anda adalah <b>OWNER</b> bot ini.\n"
            "Bebas mengakses semua fitur tanpa batas. 🛡️\n"
        )

    elif status["type"] == "permanent":
        trial_line = "✅ <b>Akses aktif</b>: PERMANENT"
        foot = "Kamu sudah mendapatkan akses permanen dari owner."

    elif status["type"] in ("1hari", "1minggu", "1bulan"):
        left = _human_left(status["left_seconds"])
        exp_fmt = datetime.fromtimestamp(status["expires_at"]).strftime("%d-%m-%Y %H:%M")
        label = {"1hari": "1 Hari", "1minggu": "1 Minggu", "1bulan": "1 Bulan"}[status["type"]]
        trial_line = f"⏳ <b>Akses aktif ({label})</b>\nSisa: <b>{left}</b>\n(hingga {exp_fmt})"
        foot = f"Kamu sudah mendapatkan akses {label.lower()} dari owner."

    elif status["type"] == "trial":
        left = _human_left(status["left_seconds"])
        exp_fmt = datetime.fromtimestamp(status["expires_at"]).strftime("%d-%m-%Y %H:%M")
        trial_line = f"⏳ <b>Sisa trial</b>: {left}\n(hingga {exp_fmt})"
        foot = f"Trial gratis sekali ({TRIAL_MINUTES} menit). Setelah habis, minta aktivasi ke owner."

    else:  # expired
        trial_line = "🔴 <b>Trial/akses habis</b>"
        foot = "Hubungi owner <a href=\"https://t.me/langrisown\">@langrisown</a> untuk melanjutkan."

    return (
        "ℹ️ <b>INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{trial_line}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ <b>Akses Bot</b>\n"
        f"• Wajib join Channel {ch}\n"
        f"• Wajib join Group {gr}\n"
        f"• {foot}\n"
    )

def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Owner", url="https://t.me/langrisown")],
        [InlineKeyboardButton("🔁 Refresh", callback_data="info_refresh")],
        [InlineKeyboardButton("🏠 Home", callback_data="back_to_main")],
    ])

class InfoHandler:
    """INFO — tampilkan status user (trial, habis, akses aktif) atau OWNER."""

    async def open(self, query, context):
        await self._render(query, context, mode="edit")

    async def refresh(self, query, context):
        await self._render(query, context, mode="edit")

    async def open_from_command_or_menu(self, message, context):
        await self._render(message, context, mode="new")

    async def _render(self, target, context, mode: str):
        try:
            if hasattr(target, "data"):  # CallbackQuery
                user = target.from_user
                msg = target.message
                can_edit = True
            else:  # Message
                user = target.from_user
                msg = target
                can_edit = False

            text = _fmt_info_text(user.id)
            kb = _keyboard()

            if mode == "edit" and can_edit:
                try:
                    await target.edit_message_text(
                        text,
                        reply_markup=kb,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    return
                except BadRequest as e:
                    if "message is not modified" in str(e).lower():
                        return
            await msg.reply_text(
                text,
                reply_markup=kb,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        except Exception as e:
            logger.error(f"[INFO] render error: {e}")
            try:
                await msg.reply_text("❌ Gagal membuka INFO.")
            except Exception:
                pass
