# access_control.py
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import storage
from config import REQUIRED_CHANNEL, REQUIRED_GROUP, show_menu, is_owner

logger = logging.getLogger(__name__)

__all__ = ["ensure_access_start", "ensure_access_feature"]

# ===== Helpers =====
def _is_admin(uid: int) -> bool:
    """Cek apakah user adalah owner/admin."""
    try:
        return is_owner(uid)  # langsung pakai helper dari config
    except Exception:
        return False

async def _check_join(bot, user_id: int) -> tuple[bool, bool]:
    """Return (joined_channel, joined_group)."""
    joined_channel = False
    joined_group = False
    try:
        if REQUIRED_CHANNEL:
            ch = await bot.get_chat_member(REQUIRED_CHANNEL, user_id)
            joined_channel = ch.status not in ("left", "kicked")
    except Exception:
        pass
    try:
        if REQUIRED_GROUP:
            gr = await bot.get_chat_member(REQUIRED_GROUP, user_id)
            joined_group = gr.status not in ("left", "kicked")
    except Exception:
        pass
    return joined_channel, joined_group

# ===== UI =====
async def _show_join_gate(target, joined_channel=False, joined_group=False, edit=False):
    """Pesan kalau belum join channel/group."""
    if joined_channel and not joined_group:
        status = "✅ Sudah join channel\n❌ Belum join group"
    elif joined_group and not joined_channel:
        status = "✅ Sudah join group\n❌ Belum join channel"
    else:
        status = "❌ Belum join channel & group"

    text = (
        "⚠️ Untuk menggunakan bot ini kamu harus join komunitas:\n\n"
        f"{status}\n\n"
        f"📢 Channel: {REQUIRED_CHANNEL}\n"
        f"💬 Group: {REQUIRED_GROUP}\n\n"
        "Setelah join, klik tombol 🔁 *Cek Lagi*."
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"),
            InlineKeyboardButton("💬 Join Group", url=f"https://t.me/{REQUIRED_GROUP.lstrip('@')}")
        ],
        [InlineKeyboardButton("🔁 Cek Lagi", callback_data="ac_check")]
    ])

    try:
        if edit and hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        elif hasattr(target, "reply_text"):
            await target.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"_show_join_gate error: {e}")

async def _show_paywall(target):
    """Pesan kalau akses habis → hubungi owner."""
    text = (
        "🔒 *Akses diperlukan*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ *Trial/Akses kamu sudah habis.*\n"
        "👤 *Hubungi owner:* @langrisown\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ Wajib join Channel: {REQUIRED_CHANNEL}\n"
        f"🛡️ Wajib join Group: {REQUIRED_GROUP}\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Owner", url="https://t.me/langrisown")],
        [InlineKeyboardButton("🏠 Home", callback_data="nav_home")]
    ])
    try:
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        else:
            await target.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"_show_paywall error: {e}")

# ===== Public API =====
async def ensure_access_start(update, context) -> bool:
    """Dipanggil saat /start atau cek lagi."""
    try:
        context.user_data.clear()
        user = update.effective_user
        if not user:
            return False
        uid = user.id

        if _is_admin(uid):
            if getattr(update, "callback_query", None):
                await show_menu(update.callback_query, "main", edit=True)
            else:
                await show_menu(update.message, "main")
            return True

        joined_channel, joined_group = await _check_join(context.bot, uid)
        if not (joined_channel and joined_group):
            if getattr(update, "callback_query", None):
                await _show_join_gate(update.callback_query, joined_channel, joined_group, edit=True)
            else:
                await _show_join_gate(update.message, joined_channel, joined_group, edit=False)
            return False

        if getattr(update, "callback_query", None):
            await show_menu(update.callback_query, "main", edit=True)
        else:
            await show_menu(update.message, "main")
        return True
    except Exception as e:
        logger.error(f"ensure_access_start error: {e}")
        return False

async def ensure_access_feature(update, context) -> bool:
    """Dipanggil saat user klik fitur/menu."""
    try:
        user = None
        msg_target = None

        if getattr(update, "callback_query", None):
            user = update.callback_query.from_user
            msg_target = update.callback_query
        elif getattr(update, "message", None):
            user = update.message.from_user
            msg_target = update.message

        if not user:
            return True

        uid = user.id
        if _is_admin(uid):
            return True

        joined_channel, joined_group = await _check_join(context.bot, uid)
        if not (joined_channel and joined_group):
            await _show_join_gate(msg_target, joined_channel, joined_group, edit=True)
            return False

        # pakai unified status dari storage
        status = storage.get_user_status(uid)
        if status["type"] in ("owner", "permanent", "1hari", "1minggu", "1bulan", "trial"):
            return True

        # expired
        await _show_paywall(msg_target)
        return False
    except Exception as e:
        logger.error(f"ensure_access_feature error: {e}")
        return True
