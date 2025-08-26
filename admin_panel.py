# admin_panel.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import OWNER_ID, TIMEZONE
import storage

logger = logging.getLogger(__name__)

# ==============================
# Callback data prefixes
# ==============================
CB_ADMIN_MENU            = "admin:menu"
CB_ADMIN_ADD             = "admin:add"
CB_ADMIN_ADD_PLAN_PERM   = "admin:add:plan:perm"
CB_ADMIN_ADD_PLAN_MONTH  = "admin:add:plan:month"
CB_ADMIN_LIST            = "admin:list"
CB_ADMIN_DELETE          = "admin:delete"
CB_ADMIN_BACK            = "admin:back"

# ==============================
# Helper UI
# ==============================
def _admin_menu_kb() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("➕ Tambah user", callback_data=CB_ADMIN_ADD),
        ],
        [
            InlineKeyboardButton("📋 List user", callback_data=CB_ADMIN_LIST),
        ],
        [
            InlineKeyboardButton("🗑️ Hapus user", callback_data=CB_ADMIN_DELETE),
        ],
        [
            InlineKeyboardButton("🏠 Kembali", callback_data="home")  # asumsi handler home sudah ada
        ]
    ]
    return InlineKeyboardMarkup(kb)

def _plan_kb() -> InlineKeyboardMarkup:
    kb = [
        [
            InlineKeyboardButton("Permanent", callback_data=CB_ADMIN_ADD_PLAN_PERM),
            InlineKeyboardButton("Sebulan",   callback_data=CB_ADMIN_ADD_PLAN_MONTH),
        ],
        [InlineKeyboardButton("⬅️ Batal", callback_data=CB_ADMIN_MENU)],
    ]
    return InlineKeyboardMarkup(kb)

def _tz():
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(TIMEZONE)
    except Exception:
        return timezone(timedelta(hours=7))  # fallback WIB

def _fmt_ts(ts: Optional[int]) -> str:
    if not ts: return "-"
    dt = datetime.fromtimestamp(ts, tz=_tz())
    return dt.strftime("%d-%m-%Y %H:%M")

# ==============================
# State flags in user_data
# ==============================
KEY_ADMIN_AWAIT_USER_ID   = "admin_await_user_id"     # True/False
KEY_ADMIN_PENDING_ACTION  = "admin_pending_action"    # "add" | "delete"
KEY_ADMIN_ADD_PLAN        = "admin_add_plan"          # "perm" | "month"
KEY_ADMIN_TEMP_ID         = "admin_temp_user_id"      # int
KEY_ADMIN_LIST_CACHE      = "admin_list_cache"        # List[Tuple[user_id, username, plan, expires_at]]

class AdminPanelHandler:
    """
    /admin hanya untuk OWNER_ID. Tiga fitur:
     - Tambah user (input ID -> pilih paket "permanent" atau "sebulan")
     - List user (tampilkan id, username / '-', paket + expiry jika bulanan)
     - Hapus user (lihat list dengan nomor urut, balas angka untuk hapus)
    Penyimpanan menggunakan storage.py (SQLite).
    """

    # ========= Entry =========
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            await update.message.reply_text("❌ Akses ditolak. Hanya owner yang dapat menggunakan /admin.")
            return

        await update.message.reply_text(
            "🛠️ *Panel Admin*\nPilih menu di bawah:",
            parse_mode="Markdown",
            reply_markup=_admin_menu_kb()
        )

    # ========= Callback router =========
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data  = query.data or ""
        user_id = query.from_user.id

        if user_id != OWNER_ID:
            await query.answer("Akses ditolak.", show_alert=True)
            return

        # Reset input states by default except when expecting a follow-up
        if data in (CB_ADMIN_MENU, CB_ADMIN_LIST, CB_ADMIN_DELETE, CB_ADMIN_ADD):
            self._clear_states(context)

        if data == CB_ADMIN_MENU:
            await query.edit_message_text("🛠️ *Panel Admin*\nPilih menu:", parse_mode="Markdown", reply_markup=_admin_menu_kb())

        elif data == CB_ADMIN_ADD:
            context.user_data[KEY_ADMIN_PENDING_ACTION] = "add"
            context.user_data[KEY_ADMIN_AWAIT_USER_ID]  = True
            await query.edit_message_text(
                "➕ *Tambah user*\n\nKirim *ID Telegram* user (angka).",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Batal", callback_data=CB_ADMIN_MENU)]])
            )

        elif data in (CB_ADMIN_ADD_PLAN_PERM, CB_ADMIN_ADD_PLAN_MONTH):
            # must have a temp user id
            temp_id = context.user_data.get(KEY_ADMIN_TEMP_ID)
            if not temp_id:
                await query.answer("Belum ada ID user. Kirim ID dulu.", show_alert=True)
                return

            plan = "perm" if data == CB_ADMIN_ADD_PLAN_PERM else "month"
            context.user_data[KEY_ADMIN_ADD_PLAN] = plan

            username = storage.get_username(temp_id)  # boleh None
            if plan == "perm":
                storage.add_or_update_subscription(user_id=temp_id, username=username, plan="permanent", expires_at=None)
                await query.edit_message_text(
                    f"✅ User *{temp_id}* ditandai *PERMANENT*.",
                    parse_mode="Markdown",
                    reply_markup=_admin_menu_kb()
                )
            else:
                expires = int((datetime.now(tz=timezone.utc) + timedelta(days=30)).timestamp())
                storage.add_or_update_subscription(user_id=temp_id, username=username, plan="monthly", expires_at=expires)
                await query.edit_message_text(
                    f"✅ User *{temp_id}* diaktifkan *SEBULAN*.\nBerakhir: { _fmt_ts(expires) } WIB",
                    parse_mode="Markdown",
                    reply_markup=_admin_menu_kb()
                )
            self._clear_states(context)

        elif data == CB_ADMIN_LIST:
            rows = storage.get_all_subscribers()
            context.user_data[KEY_ADMIN_LIST_CACHE] = rows  # cache for delete flow
            if not rows:
                await query.edit_message_text("📋 *List user kosong.*", parse_mode="Markdown", reply_markup=_admin_menu_kb())
                return

            text = self._build_list_text(rows)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_admin_menu_kb(), disable_web_page_preview=True)

        elif data == CB_ADMIN_DELETE:
            rows = storage.get_all_subscribers()
            context.user_data[KEY_ADMIN_LIST_CACHE] = rows
            if not rows:
                await query.edit_message_text("Tidak ada user untuk dihapus.", reply_markup=_admin_menu_kb())
                return
            context.user_data[KEY_ADMIN_PENDING_ACTION] = "delete"
            context.user_data[KEY_ADMIN_AWAIT_USER_ID]  = True  # reuse input channel
            guide = self._build_list_text(rows)
            guide += "\n\n🗑️ *Ketik nomor urut* user yang ingin dihapus."
            await query.edit_message_text(guide, parse_mode="Markdown",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Batal", callback_data=CB_ADMIN_MENU)]]))

        else:
            await query.answer()  # no-op

    # ========= Text input (ID / index) =========
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Dipanggil oleh MessageHandler(filters.TEXT & ~filters.COMMAND) di main.py"""
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            return  # only owner

        if not context.user_data.get(KEY_ADMIN_AWAIT_USER_ID):
            return  # not in admin input mode

        pending = context.user_data.get(KEY_ADMIN_PENDING_ACTION)
        txt = (update.message.text or "").strip()

        if pending == "add":
            # expecting a Telegram ID number
            if not txt.isdigit():
                await update.message.reply_text("❗ ID harus angka. Coba kirim lagi.")
                return
            target_id = int(txt)
            context.user_data[KEY_ADMIN_TEMP_ID] = target_id
            # ask plan
            await update.message.reply_text(
                f"ID diterima: *{target_id}*\nPilih paket langganan:",
                parse_mode="Markdown",
                reply_markup=_plan_kb()
            )
            # keep awaiting until plan chosen
            return

        elif pending == "delete":
            # expecting a list index number
            if not txt.isdigit():
                await update.message.reply_text("❗ Nomor urut harus angka. Kirim lagi.")
                return
            idx = int(txt)
            cached: List[Tuple[int, Optional[str], str, Optional[int]]] = context.user_data.get(KEY_ADMIN_LIST_CACHE) or []
            if not 1 <= idx <= len(cached):
                await update.message.reply_text("❗ Nomor urut tidak valid.")
                return
            uid, _uname, _plan, _exp = cached[idx - 1]
            storage.delete_user(uid)
            await update.message.reply_text(f"✅ User {uid} dihapus.")
            # refresh list
            rows = storage.get_all_subscribers()
            context.user_data[KEY_ADMIN_LIST_CACHE] = rows
            self._clear_states(context)
            return

    # ========= Builders =========
    def _build_list_text(self, rows: List[Tuple[int, Optional[str], str, Optional[int]]]) -> str:
        """
        rows: [(user_id, username, plan, expires_at)]
        """
        lines = ["📋 *List User*",
                 "Format: No. — ID — Username — Paket — Expired(WIB)"]
        for i, (uid, uname, plan, exp) in enumerate(rows, start=1):
            uname = uname or "-"
            pkg   = "permanent" if plan == "permanent" else "bulanan"
            exp_s = "-" if plan == "permanent" else _fmt_ts(exp)
            lines.append(f"{i}. {uid} — {uname} — {pkg} — {exp_s}")
        return "\n".join(lines)

    def _clear_states(self, context: ContextTypes.DEFAULT_TYPE):
        for k in [KEY_ADMIN_AWAIT_USER_ID, KEY_ADMIN_PENDING_ACTION, KEY_ADMIN_ADD_PLAN, KEY_ADMIN_TEMP_ID]:
            context.user_data.pop(k, None)
