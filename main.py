# main.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

from config import BOT_TOKEN, show_menu
from features.text_to_vcf import TextToVCFHandler
from features.txt_to_vcf import TxtToVCFHandler
from features.vcf_to_txt import VCFToTxtHandler
from features.merge_files import MergeFilesHandler
from features.count_files import CountFilesHandler
from features.create_group_name import CreateGroupNameHandler
from features.add_ctc_vcf import AddCtcVcfHandler
from features.remove_ctc_vcf import RemoveCtcVcfHandler
from features.edit_ctc_name import EditCtcNameHandler
from features.get_name_file import GetNameFileHandler

from admin_panel import AdminPanelHandler          # di root
from info import InfoHandler                       # di root

import storage
from access_control import ensure_access_start, ensure_access_feature

# =========================
# Logging
# =========================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class VCFGeneratorBot:
    def __init__(self):
        storage.init_db()
        self.app = Application.builder().token(BOT_TOKEN).build()
        self._setup_handlers()

    # =========================
    # Register Handlers
    # =========================
    def _setup_handlers(self):
        # Commands
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("info", self.cmd_info))
        self.app.add_handler(CommandHandler("admin", self.cmd_admin))

        # Callback buttons
        self.app.add_handler(CallbackQueryHandler(self.on_callback))

        # Documents
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.on_document))

        # Free text
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

        # Error handler biar trace tidak “No error handlers are registered”
        self.app.add_error_handler(self.on_error)

    # =========================
    # Commands
    # =========================
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Tampilkan menu utama (akses gate di-start)."""
        context.user_data.clear()
        await ensure_access_start(update, context)  # handler gate-mu akan memanggil show_menu(...)

    async def cmd_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/info — panggil InfoHandler.start (bukan open_from_command_or_menu)."""
        allowed = await ensure_access_feature(update, context)
        if not allowed:
            return
        # FIX: gunakan method yang ada
        await InfoHandler().start(update, context)

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/admin — khusus owner; panggil AdminPanelHandler.start."""
        allowed = await ensure_access_feature(update, context)
        if not allowed:
            return
        # FIX: gunakan method yang ada
        await AdminPanelHandler().start(update, context)

    # =========================
    # Callback Buttons
    # =========================
    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = (query.data or "").strip()
        await query.answer()

        # ===== Admin callbacks (delegasi langsung) =====
        if data.startswith("admin:"):
            return await AdminPanelHandler().handle_callback(update, context)

        # ====== ACCESS CONTROL legacy buttons (kompat) ======
        if data == "ac_check":
            await ensure_access_start(update, context)
            return
        if data == "ac_open_pay":
            await query.edit_message_text(
                "👤 Silakan hubungi owner @pudidi untuk mengaktifkan akses.",
                parse_mode="Markdown"
            )
            return

        # ====== NAVIGASI MENU (pakai show_menu milikmu) ======
        if data in ("nav_p1_left", "nav_p1_right"):
            await show_menu(query, "main_page2", edit=True); return
        if data in ("nav_p2_left", "nav_p2_right", "nav_home", "back_to_main"):
            await show_menu(query, "main", edit=True); return

        # ====== INFO (refresh dalam halaman INFO) ======
        if data == "info_refresh":
            allowed = await ensure_access_feature(update, context)
            if not allowed:
                return
            await InfoHandler().refresh(query, context)
            return

        # ====== Pastikan akses aktif sebelum fitur lain ======
        allowed = await ensure_access_feature(update, context)
        if not allowed:
            return

        # ====== ROUTING MENU/SUBMENU KHUSUS ======
        if data == "text_to_vcf":
            await show_menu(query, "text_submenu", edit=True); return
        if data == "cv_txt_to_vcf":
            await show_menu(query, "cv_submenu", edit=True); return
        if data == "merge_files":
            await show_menu(query, "merge_submenu", edit=True); return

        # ====== TEXT → VCF (ADMIN/NAVY) ======
        if data == "text_format":
            await TextToVCFHandler().start_text_mode(update, context); return
        if data == "text_input":
            await TextToVCFHandler().start_input_mode(update, context); return
        if data in ("input_add_navy", "input_admin_only"):
            await TextToVCFHandler().handle_input_choice(query, context); return

        # ====== TXT → VCF (DIRECT/BATCH V1/V2) ======
        if data in ("cv_v1", "cv_v2", "output_default", "output_custom", "v2_proceed", "v2_format", "v2_input"):
            await TxtToVCFHandler().handle_callback(query, context); return

        # ====== VCF → TXT ======
        if data == "cv_vcf_to_txt":
            await VCFToTxtHandler().start_vcf_mode(update, context); return
        if data in ("vcf_separate", "vcf_merge"):
            await VCFToTxtHandler().handle_callback(query, context); return

        # ====== MERGE TXT/VCF ======
        if data in ("merge_txt", "merge_vcf"):
            await MergeFilesHandler().handle_callback(query, context); return

        # ====== COUNT FILES ======
        if data == "count_files":
            await CountFilesHandler().start_mode(query, context); return

        # ====== CREATE GROUP NAME ======
        if data == "create_group_name":
            await CreateGroupNameHandler().start_mode(query, context); return

        # ====== FITUR TAMBAHAN (ADD/REMOVE/EDIT/GET NAME) ======
        if data == "add_ctc_vcf":
            await AddCtcVcfHandler().start_mode(query, context); return
        if data in ("addctc_name", "addctc_done"):
            await AddCtcVcfHandler().handle_callback(query, context); return
        if data == "remove_ctc_vcf":
            await RemoveCtcVcfHandler().start_mode(query, context); return
        if data == "edit_ctc_name":
            await EditCtcNameHandler().start_mode(query, context); return
        if data == "get_name_file":
            await GetNameFileHandler().start_mode(query, context); return

        # ====== Fallback ======
        await query.edit_message_text(
            "🚧 Fitur ini akan segera hadir!\n\nGunakan /start untuk kembali ke menu utama.",
            parse_mode="Markdown"
        )

    # =========================
    # Documents
    # =========================
    async def on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        allowed = await ensure_access_feature(update, context)
        if not allowed:
            return

        # COUNT
        if context.user_data.get("waiting_for_count_files"):
            await CountFilesHandler().handle_document(update, context); return

        # TXT → VCF (V1/V2)
        if context.user_data.get("waiting_for_txt_files"):
            await TxtToVCFHandler().handle_document(update, context); return

        # VCF → TXT
        if context.user_data.get("waiting_for_vcf_files"):
            await VCFToTxtHandler().handle_document(update, context); return

        # MERGE
        if context.user_data.get("waiting_for_merge_txt_files"):
            await MergeFilesHandler().handle_document(update, context, "txt"); return
        if context.user_data.get("waiting_for_merge_vcf_files"):
            await MergeFilesHandler().handle_document(update, context, "vcf"); return

        # ADD/REMOVE/EDIT/GET-NAME
        if context.user_data.get("waiting_for_add_vcf_file"):
            await AddCtcVcfHandler().handle_document(update, context); return
        if context.user_data.get("waiting_for_remove_vcf_file"):
            await RemoveCtcVcfHandler().handle_document(update, context); return
        if context.user_data.get("waiting_for_edit_vcf_files"):
            await EditCtcNameHandler().handle_document(update, context); return
        if context.user_data.get("waiting_for_getname_files"):
            await GetNameFileHandler().handle_document(update, context); return

        await update.message.reply_text(
            "❌ Silakan gunakan menu untuk memulai proses atau upload file dengan format yang benar."
        )

    # =========================
    # Free Text
    # =========================
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        allowed = await ensure_access_feature(update, context)
        if not allowed:
            return

        # CREATE GROUP NAME
        if any(key in context.user_data for key in ("waiting_for_group_basename", "waiting_for_group_count")):
            await CreateGroupNameHandler().handle_text(update, context); return

        # TEXT → VCF (mode format lama)
        if context.user_data.get("waiting_for_string"):
            await TextToVCFHandler().handle_text_input(update, context); return

        # TEXT → VCF (wizard INPUT)
        if any(key in context.user_data for key in (
            "waiting_for_admin_phone", "waiting_for_admin_name", "waiting_for_choice",
            "waiting_for_navy_phone", "waiting_for_navy_name", "waiting_for_filename"
        )):
            await TextToVCFHandler().handle_text_input(update, context); return

        # TXT → VCF (V1 & V2 prompt)
        if any(key in context.user_data for key in (
            "waiting_for_v2_format", "waiting_for_contact_name",
            "waiting_for_custom_filename", "waiting_for_custom_contact_name",
            "v2_input_step"
        )):
            await TxtToVCFHandler().handle_text_input(update, context); return

        # VCF → TXT (merge filename)
        if context.user_data.get("waiting_for_merge_filename"):
            await VCFToTxtHandler().handle_text_input(update, context); return

        # MERGE (txt/vcf)
        if any(key in context.user_data for key in (
            "waiting_for_merge_txt_filename", "waiting_for_merge_vcf_filename"
        )):
            await MergeFilesHandler().handle_text_input(update, context); return

        # ADD/REMOVE/EDIT
        if context.user_data.get("waiting_for_phone_to_add"):
            await AddCtcVcfHandler().handle_text(update, context); return
        if context.user_data.get("waiting_for_batch_name"):
            await AddCtcVcfHandler().handle_text(update, context); return
        if context.user_data.get("waiting_for_phone_to_remove"):
            await RemoveCtcVcfHandler().handle_text(update, context); return
        if context.user_data.get("waiting_for_edit_name"):
            await EditCtcNameHandler().handle_text(update, context); return

        # Admin text flow (hapus/ tambah user by index/ID) – biar aman tetap delegasi ke admin panel kalau owner
        if update.effective_user and update.effective_user.id:
            await AdminPanelHandler().handle_text(update, context); return

        await update.message.reply_text(
            "❌ Tidak ada operasi yang menunggu input. Gunakan /start untuk memulai."
        )

    # =========================
    # Error Handler
    # =========================
    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.exception("Unhandled exception", exc_info=context.error)

    # =========================
    # Runner
    # =========================
    def run(self):
        logger.info("🤖 VCF Generator Bot is running...")
        self.app.run_polling()


if __name__ == "__main__":
    VCFGeneratorBot().run()
