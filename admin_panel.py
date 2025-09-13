# admin_panel.py
import logging
import io
import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import xlsxwriter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ContextTypes

from config import TIMEZONE, is_owner
import storage

logger = logging.getLogger(__name__)

# ==============================
# Callback data
# ==============================
CB_ADMIN_MENU          = "admin:menu"
CB_ADMIN_ADD           = "admin:add"
CB_ADMIN_ADD_PLAN_PERM = "admin:add:plan:perm"
CB_ADMIN_ADD_PLAN_DAY  = "admin:add:plan:day"
CB_ADMIN_ADD_PLAN_WEEK = "admin:add:plan:week"
CB_ADMIN_ADD_PLAN_MONTH= "admin:add:plan:month"
CB_ADMIN_LIST_PAGE     = "admin:list:page"
CB_ADMIN_LIST_EXPIRED  = "admin:list:expired"
CB_ADMIN_SEARCH        = "admin:search"
CB_ADMIN_DELETE        = "admin:delete"
CB_ADMIN_DELETE_ALL    = "admin:delete:all"
CB_ADMIN_DELETE_PERM   = "admin:delete:perm"
CB_ADMIN_DELETE_DAY    = "admin:delete:day"
CB_ADMIN_DELETE_WEEK   = "admin:delete:week"
CB_ADMIN_DELETE_MONTH  = "admin:delete:month"
CB_ADMIN_SUMMARY       = "admin:summary"
CB_ADMIN_EXPORT        = "admin:export"
CB_ADMIN_EXPORT_DB     = "admin:exportdb"
CB_ADMIN_IMPORT_DB     = "admin:importdb"
CB_ADMIN_BROADCAST     = "admin:broadcast"

PAGE_SIZE = 10

# ==============================
# UI Builders
# ==============================
def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Tambah", callback_data=CB_ADMIN_ADD),
         InlineKeyboardButton("🗑️ Hapus", callback_data=CB_ADMIN_DELETE)],
        [InlineKeyboardButton("📋 List Aktif", callback_data=f"{CB_ADMIN_LIST_PAGE}:1"),
         InlineKeyboardButton("📕 List Expired", callback_data=CB_ADMIN_LIST_EXPIRED)],
        [InlineKeyboardButton("📊 Ringkasan", callback_data=CB_ADMIN_SUMMARY),
         InlineKeyboardButton("📂 Export Excel", callback_data=CB_ADMIN_EXPORT)],
        [InlineKeyboardButton("📤 Export DB", callback_data=CB_ADMIN_EXPORT_DB),
         InlineKeyboardButton("📥 Import DB", callback_data=CB_ADMIN_IMPORT_DB)],
        [InlineKeyboardButton("📢 Broadcast", callback_data=CB_ADMIN_BROADCAST),
         InlineKeyboardButton("🔍 Cari User", callback_data=CB_ADMIN_SEARCH)],
    ])

def _plan_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Permanent", callback_data=CB_ADMIN_ADD_PLAN_PERM)],
        [InlineKeyboardButton("1 Hari", callback_data=CB_ADMIN_ADD_PLAN_DAY),
         InlineKeyboardButton("1 Minggu", callback_data=CB_ADMIN_ADD_PLAN_WEEK)],
        [InlineKeyboardButton("1 Bulan", callback_data=CB_ADMIN_ADD_PLAN_MONTH)],
        [InlineKeyboardButton("⬅️ Batal", callback_data=CB_ADMIN_MENU)]
    ])

def _delete_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Semua", callback_data=CB_ADMIN_DELETE_ALL),
         InlineKeyboardButton("🗑️ Permanent", callback_data=CB_ADMIN_DELETE_PERM)],
        [InlineKeyboardButton("🗑️ 1 Hari", callback_data=CB_ADMIN_DELETE_DAY),
         InlineKeyboardButton("🗑️ 1 Minggu", callback_data=CB_ADMIN_DELETE_WEEK)],
        [InlineKeyboardButton("🗑️ 1 Bulan", callback_data=CB_ADMIN_DELETE_MONTH)],
        [InlineKeyboardButton("⬅️ Kembali", callback_data=CB_ADMIN_MENU)],
    ])

def _tz():
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(TIMEZONE)
    except Exception:
        return timezone(timedelta(hours=7))

def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "-"
    dt = datetime.fromtimestamp(ts, tz=_tz())
    return dt.strftime("%d-%m-%Y %H:%M")

# ==============================
# State flags
# ==============================
KEY_ADMIN_PENDING_ACTION = "admin_pending_action"
KEY_ADMIN_AWAIT_USER_ID  = "admin_await_user_id"
KEY_ADMIN_EXPECT_NAME    = "admin_expect_name"
KEY_ADMIN_TEMP_ID        = "admin_temp_user_id"
KEY_ADMIN_TEMP_NAME      = "admin_temp_user_name"
KEY_ADMIN_BROADCAST_WAIT = "admin_broadcast_wait"
KEY_ADMIN_IMPORT_WAIT    = "admin_import_wait"
KEY_ADMIN_SEARCH_WAIT    = "admin_search_wait"

# ==============================
# Handler Class
# ==============================
class AdminPanelHandler:
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update.effective_user.id):
            return await update.message.reply_text("❌ Akses ditolak. Hanya owner yang bisa /admin.")
        await update.message.reply_text("🛠️ *Panel Admin*\nPilih menu:",
                                        parse_mode="Markdown", reply_markup=_admin_menu_kb())

    # ------------------------------------------------
    # Callback Handler
    # ------------------------------------------------
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        data = q.data
        if not is_owner(q.from_user.id):
            return await q.answer("Akses ditolak.", show_alert=True)

        # === Menu utama ===
        if data == CB_ADMIN_MENU:
            return await q.edit_message_text("🛠️ *Panel Admin*\nPilih menu:",
                                             parse_mode="Markdown", reply_markup=_admin_menu_kb())

        # === Import DB ===
        if data == CB_ADMIN_IMPORT_DB:
            context.user_data[KEY_ADMIN_PENDING_ACTION] = "importdb"
            context.user_data[KEY_ADMIN_IMPORT_WAIT] = True
            return await q.edit_message_text("📥 Kirim file `users.db` untuk import (akan timpa data lama).",
                                             parse_mode="Markdown")

        # === Export DB ===
        if data == CB_ADMIN_EXPORT_DB:
            try:
                with open(storage.DB_PATH, "rb") as f:
                    return await q.message.reply_document(InputFile(f, "users.db"), caption="📂 Export DB sukses")
            except Exception as e:
                return await q.edit_message_text(f"❌ Gagal export DB: {e}")

        # === Hapus user ===
        if data == CB_ADMIN_DELETE:
            context.user_data[KEY_ADMIN_PENDING_ACTION] = "delete"
            return await q.edit_message_text("🗑️ *Hapus User*\n\nKirim ID/Nama user untuk menghapus, atau pilih opsi massal:",
                                             parse_mode="Markdown", reply_markup=_delete_kb())

        if data in (CB_ADMIN_DELETE_ALL, CB_ADMIN_DELETE_PERM, CB_ADMIN_DELETE_DAY,
                    CB_ADMIN_DELETE_WEEK, CB_ADMIN_DELETE_MONTH):
            rows = storage.get_all_subscribers()
            for uid, _, plan, _ in rows:
                if data == CB_ADMIN_DELETE_ALL:
                    storage.delete_user(uid)
                elif data == CB_ADMIN_DELETE_PERM and plan == "permanent":
                    storage.delete_user(uid)
                elif data == CB_ADMIN_DELETE_DAY and plan == "1hari":
                    storage.delete_user(uid)
                elif data == CB_ADMIN_DELETE_WEEK and plan == "1minggu":
                    storage.delete_user(uid)
                elif data == CB_ADMIN_DELETE_MONTH and plan == "1bulan":
                    storage.delete_user(uid)
            return await q.edit_message_text("✅ Penghapusan selesai.", reply_markup=_admin_menu_kb())

        # === Broadcast ===
        if data == CB_ADMIN_BROADCAST:
            context.user_data[KEY_ADMIN_PENDING_ACTION] = "broadcast"
            context.user_data[KEY_ADMIN_BROADCAST_WAIT] = True
            return await q.edit_message_text("📢 Ketik pesan broadcast yang ingin dikirim ke semua user:",
                                             parse_mode="Markdown")

        # === Cari user ===
        if data == CB_ADMIN_SEARCH:
            context.user_data[KEY_ADMIN_PENDING_ACTION] = "search"
            context.user_data[KEY_ADMIN_SEARCH_WAIT] = True
            return await q.edit_message_text("🔍 Kirim *ID atau Nama* user yang ingin dicari:",
                                             parse_mode="Markdown")

        # === Tambah user ===
        if data == CB_ADMIN_ADD:
            context.user_data[KEY_ADMIN_PENDING_ACTION] = "add"
            context.user_data[KEY_ADMIN_AWAIT_USER_ID] = True
            return await q.edit_message_text("➕ Kirim *ID Telegram* user:", parse_mode="Markdown")

        # === Tambah user pilih plan ===
        if data in (CB_ADMIN_ADD_PLAN_PERM, CB_ADMIN_ADD_PLAN_DAY,
                    CB_ADMIN_ADD_PLAN_WEEK, CB_ADMIN_ADD_PLAN_MONTH):
            uid = context.user_data.get(KEY_ADMIN_TEMP_ID)
            name = context.user_data.get(KEY_ADMIN_TEMP_NAME, "-")
            if not uid:
                return await q.answer("Belum ada ID user.", show_alert=True)

            if data == CB_ADMIN_ADD_PLAN_PERM:
                storage.add_or_update_subscription(uid, name, "permanent", None)
                msg = f"✅ User {uid} ({name}) PERMANENT."
            elif data == CB_ADMIN_ADD_PLAN_DAY:
                exp = int((datetime.now(tz=timezone.utc) + timedelta(days=1)).timestamp())
                storage.add_or_update_subscription(uid, name, "1hari", exp)
                msg = f"✅ User {uid} ({name}) aktif 1 Hari."
            elif data == CB_ADMIN_ADD_PLAN_WEEK:
                exp = int((datetime.now(tz=timezone.utc) + timedelta(weeks=1)).timestamp())
                storage.add_or_update_subscription(uid, name, "1minggu", exp)
                msg = f"✅ User {uid} ({name}) aktif 1 Minggu."
            else:
                exp = int((datetime.now(tz=timezone.utc) + timedelta(days=30)).timestamp())
                storage.add_or_update_subscription(uid, name, "1bulan", exp)
                msg = f"✅ User {uid} ({name}) aktif 1 Bulan."
            return await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=_admin_menu_kb())

        # === List aktif & expired ===
        if data.startswith(f"{CB_ADMIN_LIST_PAGE}:"):
            page = int(data.split(":")[-1])
            rows = storage.get_active_subscribers()
            if not rows:
                return await q.edit_message_text("📋 *Tidak ada user aktif.*", parse_mode="Markdown",
                                                 reply_markup=_admin_menu_kb())
            text, kb = self._build_list_page(rows, page, active=True)
            return await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

        if data == CB_ADMIN_LIST_EXPIRED:
            rows = storage.get_expired_subscribers()
            if not rows:
                return await q.edit_message_text("📕 *Tidak ada user expired.*", parse_mode="Markdown",
                                                 reply_markup=_admin_menu_kb())
            lines = ["📕 *List User Expired*"]
            for i, u in enumerate(rows, start=1):
                lines.append(f"{i}. {u['user_id']} | {u['name'] or '-'} | {u['plan']} | Exp: {_fmt_ts(u['expires_at'])}")
            return await q.edit_message_text("\n".join(lines), parse_mode="Markdown",
                                             reply_markup=_admin_menu_kb())

        # === Ringkasan ===
        if data == CB_ADMIN_SUMMARY:
            rows = storage.get_all_subscribers()
            total = len(rows)
            perm = sum(1 for _, _, p, _ in rows if p == "permanent")
            d1 = sum(1 for _, _, p, _ in rows if p == "1hari")
            w1 = sum(1 for _, _, p, _ in rows if p == "1minggu")
            m1 = sum(1 for _, _, p, _ in rows if p == "1bulan")
            msg = (f"📊 *Ringkasan User*\n"
                   f"• Total: {total}\n"
                   f"• Permanent: {perm}\n"
                   f"• 1 Hari: {d1}\n"
                   f"• 1 Minggu: {w1}\n"
                   f"• 1 Bulan: {m1}")
            return await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=_admin_menu_kb())

        # === Export Excel ===
        if data == CB_ADMIN_EXPORT:
            rows = storage.get_all_subscribers()
            if not rows:
                return await q.edit_message_text("Tidak ada user.", reply_markup=_admin_menu_kb())
            output = io.BytesIO()
            wb = xlsxwriter.Workbook(output, {'in_memory': True})
            ws = wb.add_worksheet("Users")
            headers = ["User ID", "Nama", "Paket", "Expired", "Status"]
            for c, h in enumerate(headers):
                ws.write(0, c, h)
            for r, (uid, name, plan, exp) in enumerate(rows, start=1):
                status = storage.get_user_status(uid)
                pkg_map = {"permanent": "Permanent","1hari": "1 Hari",
                           "1minggu": "1 Minggu","1bulan": "1 Bulan"}
                pkg = pkg_map.get(plan, plan)
                exp_val = "PERMANENT" if plan == "permanent" else _fmt_ts(exp)
                ws.write(r, 0, uid)
                ws.write(r, 1, name or "-")
                ws.write(r, 2, pkg)
                ws.write(r, 3, exp_val)
                ws.write(r, 4, status["type"])
            wb.close()
            output.seek(0)
            return await q.message.reply_document(InputFile(output, "subscribers.xlsx"),
                                                  caption="📂 Export data user")

    # ------------------------------------------------
    # Handle Document (Import DB)
    # ------------------------------------------------
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update.effective_user.id):
            return

        pending = context.user_data.get(KEY_ADMIN_PENDING_ACTION)
        if pending != "importdb" or not context.user_data.get(KEY_ADMIN_IMPORT_WAIT):
            return await update.message.reply_text("❌ Silakan pilih menu Import DB terlebih dahulu.")

        context.user_data[KEY_ADMIN_IMPORT_WAIT] = False
        try:
            doc = update.message.document
            if not doc or not doc.file_name.endswith(".db"):
                return await update.message.reply_text("❌ File harus `.db`")
            tg_file = await update.get_bot().get_file(doc.file_id)
            data = await tg_file.download_as_bytearray()
            with open(storage.DB_PATH, "wb") as f:
                f.write(data)
            return await update.message.reply_text("✅ Import DB sukses. Restart bot untuk menerapkan perubahan.")
        except Exception as e:
            logger.error(f"Gagal import DB: {e}")
            return await update.message.reply_text(f"❌ Gagal import DB: {e}")

    # ------------------------------------------------
    # Handle Text (Cari, Tambah, Hapus, Broadcast)
    # ------------------------------------------------
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update.effective_user.id):
            return
        txt = update.message.text.strip()
        pending = context.user_data.get(KEY_ADMIN_PENDING_ACTION)

        # === Cari user ===
        if pending == "search" and context.user_data.get(KEY_ADMIN_SEARCH_WAIT):
            context.user_data[KEY_ADMIN_SEARCH_WAIT] = False
            rows = storage.get_all_subscribers()
            if txt.isdigit():
                uid = int(txt)
                detail = storage.get_user_detail(uid)
                if detail:
                    return await update.message.reply_text(
                        f"👤 User ditemukan:\n"
                        f"ID: {detail['user_id']}\n"
                        f"Nama: {detail['name'] or '-'}\n"
                        f"Plan: {detail['plan']}\n"
                        f"Status: {detail['type']}\n"
                        f"Expired: {_fmt_ts(detail['expires_at'])}")
            else:
                for uid, name, _, _ in rows:
                    if name and name.lower() == txt.lower():
                        detail = storage.get_user_detail(uid)
                        return await update.message.reply_text(
                            f"👤 User ditemukan:\n"
                            f"ID: {detail['user_id']}\n"
                            f"Nama: {detail['name'] or '-'}\n"
                            f"Plan: {detail['plan']}\n"
                            f"Status: {detail['type']}\n"
                            f"Expired: {_fmt_ts(detail['expires_at'])}")
            return await update.message.reply_text("❗ User tidak ditemukan.")

        # === Broadcast ===
        if pending == "broadcast" and context.user_data.get(KEY_ADMIN_BROADCAST_WAIT):
            context.user_data[KEY_ADMIN_BROADCAST_WAIT] = False
            users = storage.get_all_users()
            success, fail = 0, 0
            for u in users:
                try:
                    await update.get_bot().send_message(chat_id=u["user_id"], text=txt)
                    success += 1
                except Exception as e:
                    logger.warning(f"Broadcast gagal ke {u['user_id']}: {e}")
                    fail += 1
                await asyncio.sleep(0.05)
            return await update.message.reply_text(f"📢 Broadcast selesai.\n✅ {success} | ❌ {fail}")

        # === Tambah user flow ===
        if pending == "add":
            if context.user_data.get(KEY_ADMIN_AWAIT_USER_ID):
                if not txt.isdigit():
                    return await update.message.reply_text("❗ ID harus angka.")
                context.user_data[KEY_ADMIN_TEMP_ID] = int(txt)
                context.user_data.pop(KEY_ADMIN_AWAIT_USER_ID, None)
                context.user_data[KEY_ADMIN_EXPECT_NAME] = True
                return await update.message.reply_text("Sekarang kirim *nama user*:", parse_mode="Markdown")

            if context.user_data.get(KEY_ADMIN_EXPECT_NAME):
                context.user_data[KEY_ADMIN_TEMP_NAME] = txt
                context.user_data.pop(KEY_ADMIN_EXPECT_NAME, None)
                return await update.message.reply_text("Pilih paket:", parse_mode="Markdown",
                                                       reply_markup=_plan_kb())

        # === Hapus user by ID/nama ===
        if pending == "delete":
            rows = storage.get_all_subscribers()
            if txt.isdigit():
                uid = int(txt)
                storage.delete_user(uid)
                return await update.message.reply_text(f"✅ User {uid} dihapus.")
            for uid, name, _, _ in rows:
                if name and name.lower() == txt.lower():
                    storage.delete_user(uid)
                    return await update.message.reply_text(f"✅ User {uid} ({name}) dihapus.")
            return await update.message.reply_text("❗ User tidak ditemukan.")

    # ------------------------------------------------
    # List Pagination
    # ------------------------------------------------
    def _build_list_page(self, rows, page: int, active=True):
        total = len(rows)
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        start, end = (page-1)*PAGE_SIZE, page*PAGE_SIZE
        chunk = rows[start:end]

        lines = ["📋 *List User Aktif*" if active else "📕 *List User Expired*"]
        lines.append("No | ID | Nama | Paket | Expired")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
        for i, u in enumerate(chunk, start=start+1):
            lines.append(f"{i}. {u['user_id']} | {u['name'] or '-'} | {u['plan']} | {_fmt_ts(u['expires_at'])}")

        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{CB_ADMIN_LIST_PAGE}:{page-1}"))
        if page < pages:
            nav.append(InlineKeyboardButton("➡️ Next", callback_data=f"{CB_ADMIN_LIST_PAGE}:{page+1}"))

        kb = [nav] if nav else []
        kb.append([InlineKeyboardButton("⬅️ Kembali", callback_data=CB_ADMIN_MENU)])
        return "\n".join(lines), InlineKeyboardMarkup(kb)
