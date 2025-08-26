# features/info.py
import logging
from datetime import datetime
from html import escape
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

import storage
from config import REQUIRED_CHANNEL, REQUIRED_GROUP

logger = logging.getLogger(__name__)

__all__ = ["InfoHandler"]

def _now_ts() -> int:
    return int(datetime.utcnow().timestamp())

def _human_left(seconds: int) -> str:
    if seconds <= 0:
        return "0m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h and m: return f"{h}j {m}m"
    if h:       return f"{h}j"
    return f"{m}m"

def _fmt_info_text(trial_left: str | None, trial_active: bool) -> str:
    """
    Bangun teks INFO dengan HTML — tanpa <br/> (pakai newline saja).
    Saat trial habis → instruksi hubungi owner @pudidi (manual).
    """
    ch = escape(REQUIRED_CHANNEL or "-")
    gr = escape(REQUIRED_GROUP or "-")

    if trial_active:
        trial_line = f"⏳ <b>Sisa trial</b>: {escape(trial_left)}"
        foot = "Trial gratis sekali (3 jam). Setelah habis, minta aktivasi ke owner."
    else:
        trial_line = "🔒 <b>Trial habis</b>"
        foot = "Hubungi owner <a href=\"https://t.me/pudidi\">@pudidi</a> untuk melanjutkan."

    text = (
        "ℹ️ <b>INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{trial_line}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ <b>Akses Bot</b>\n"
        f"• Wajib join Channel {ch}\n"
        f"• Wajib join Group {gr}\n"
        f"• {foot}\n"
    )
    return text

def _keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Owner", url="https://t.me/pudidi")],
        [InlineKeyboardButton("🔁 Refresh", callback_data="info_refresh")],
        [InlineKeyboardButton("🏠 Home", callback_data="back_to_main")]
    ])

class InfoHandler:
    """
    INFO — sisa trial + syarat akses + kontak owner (manual).
    Tombol: Owner (URL), Refresh (callback), Home (callback).
    """

    async def open(self, query, context):
        await self._render(query, context, mode="edit")

    async def refresh(self, query, context):
        await self._render(query, context, mode="edit")  # cukup edit

    async def open_from_command_or_menu(self, message, context):
        await self._render(message, context, mode="new")

    async def _render(self, target, context, mode: str):
        """
        mode:
          - 'edit' : edit pesan jika memungkinkan (dipakai tombol & pertama kali)
          - 'new'  : kirim pesan baru (dipakai /info)
        """
        try:
            if hasattr(target, "from_user"):  # CallbackQuery
                user = target.from_user
                msg = target.message
                can_edit = True
            else:  # Message
                user = target.from_user
                msg = target
                can_edit = False

            u = storage.get_or_create_user(user.id)
            now = _now_ts()
            trial_end = int(u.get("trial_end") or 0)
            trial_active = trial_end > now
            trial_left = _human_left(trial_end - now) if trial_active else "-"

            text = _fmt_info_text(trial_left, trial_active)
            kb = _keyboard()

            if mode == "edit" and can_edit:
                try:
                    await target.edit_message_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
                    return
                except BadRequest as e:
                    if "message is not modified" in str(e).lower():
                        return
                    # Fallback: kirim pesan baru
                    pass

            await msg.reply_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

        except Exception as e:
            logger.error(f"[INFO] render error: {e}")
            try:
                await msg.reply_text("❌ Gagal membuka INFO.")
            except Exception:
                pass
