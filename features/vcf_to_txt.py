# features/vcf_to_txt.py
import io
import time
import asyncio
from telegram import InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from config import UPLOAD_TIMEOUT, SLEEP_BETWEEN_FILES

SESSION_BUCKET = "vcf_to_txt_sessions"  # key di chat_data

class VCFToTxtHandler:
    """
    VCF → TXT (status di 1 pesan)
    """

    # =========================
    # Entry
    # =========================
    async def start_vcf_mode(self, update, context):
        context.user_data.clear()
        context.user_data.update({
            "waiting_for_vcf_files": True,
            "vcf_files": [],            # list[{"filename","content","count"}]
            "vcf_last_ts": 0.0,
            "vcf_preview_msg": None,   # Message ringkasan
            "vcf_finalize_task": None,
            "waiting_for_merge_filename": False,
            "vcf_session_msg_id": None # message_id ringkasan untuk mode gabung
        })
        await update.callback_query.edit_message_text(
            "📂 *Upload file .vcf untuk dikonversi ke TXT.*\n"
            "Kamu bisa upload beberapa file sekaligus.",
            parse_mode="Markdown"
        )

    # =========================
    # Upload handler
    # =========================
    async def handle_document(self, update, context):
        if not context.user_data.get("waiting_for_vcf_files"):
            return

        doc = update.message.document
        if not doc or not str(doc.file_name).lower().endswith(".vcf"):
            await update.message.reply_text("❌ Hanya menerima file .vcf")
            return

        try:
            tg = await context.bot.get_file(doc.file_id)
            data = await tg.download_as_bytearray()
            content = data.decode("utf-8", errors="ignore")
        except Exception:
            await update.message.reply_text(f"❌ Gagal membaca `{doc.file_name}`", parse_mode="Markdown")
            return

        count = self._count_tel(content)
        context.user_data["vcf_files"].append({
            "filename": doc.file_name,
            "content": content,
            "count": count
        })
        context.user_data["vcf_last_ts"] = time.time()

        await self._show_preview(update, context, final=False)
        self._schedule_finalize(update, context)

    # =========================
    # Preview builder
    # =========================
    def _build_status_line(self, final: bool) -> str:
        return "✅ *Selesai membaca semua file.*" if final else "🔄 *Tunggu sebentar, bot sedang membaca file…*"

    def _build_preview_text(self, files: list, final: bool) -> str:
        file_count = len(files)
        total_numbers = sum(f["count"] for f in files)

        header = "📤 *Ringkasan Upload*\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        body = "📁 *Daftar File:*\n"

        display_limit = 15
        display_files = files[-display_limit:] if file_count > display_limit else files
        start_idx = file_count - len(display_files) + 1
        for idx, f in enumerate(display_files, start=start_idx):
            body += f"{idx}. `{f['filename']}` — 📞 {f['count']} nomor\n"

        status = f"{self._build_status_line(final)}\n\n"
        footer = "━━━━━━━━━━━━━━━━━━━━━━━\n" \
                 f"🧮 *Total:* {file_count} file · {total_numbers} nomor"

        return header + body + status + footer

    async def _show_preview(self, update, context, final: bool):
        files = context.user_data.get("vcf_files", [])
        if not files:
            return

        text = self._build_preview_text(files, final)

        keyboard = None
        if final:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📄 Selesai", callback_data="vcf_separate"),
                InlineKeyboardButton("🔗 Gabung",  callback_data="vcf_merge"),
            ]])

        key = "vcf_preview_msg"
        msg_obj = None

        if context.user_data.get(key):
            try:
                edited = await context.user_data[key].edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
                msg_obj = edited or context.user_data[key]
            except Exception:
                sent = await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
                context.user_data[key] = sent
                msg_obj = sent
        else:
            sent = await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
            context.user_data[key] = sent
            msg_obj = sent

        # === simpan sesi per-pesan di chat_data ===
        session_bucket = context.chat_data.setdefault(SESSION_BUCKET, {})
        # simpan shallow copy agar tidak keubah di tempat lain
        session_bucket[msg_obj.message_id] = {
            "files": [dict(f) for f in files],
            "ts": time.time(),
            "user_id": update.effective_user.id if update and update.effective_user else None
        }
        # simpan juga message_id di user_data untuk alur "Gabung"
        context.user_data["vcf_session_msg_id"] = msg_obj.message_id

    # =========================
    # Debounce finalize
    # =========================
    def _schedule_finalize(self, update, context):
        old = context.user_data.get("vcf_finalize_task")
        if old and not old.done():
            old.cancel()

        async def waiter():
            try:
                await asyncio.sleep(UPLOAD_TIMEOUT)
                last = context.user_data.get("vcf_last_ts", 0.0)
                if time.time() - last >= UPLOAD_TIMEOUT - 0.05:
                    await self._finalize(update, context)
            except asyncio.CancelledError:
                pass

        context.user_data["vcf_finalize_task"] = asyncio.create_task(waiter())

    async def _finalize(self, update, context):
        if not context.user_data.get("vcf_files"):
            return
        context.user_data["waiting_for_vcf_files"] = False
        await self._show_preview(update, context, final=True)

        task = context.user_data.pop("vcf_finalize_task", None)
        if task and not task.done():
            task.cancel()

    # =========================
    # Callback buttons
    # =========================
    async def handle_callback(self, query, context):
        if query.data == "vcf_separate":
            await self._export_separate(query, context)
        elif query.data == "vcf_merge":
            await self._ask_merge_filename(query, context)

    async def _export_separate(self, query, context):
        # Ambil dari sesi per-pesan terlebih dulu
        sessions = context.chat_data.get(SESSION_BUCKET, {})
        session = sessions.get(query.message.message_id)

        files = []
        if session and session.get("files"):
            files = session["files"]
        else:
            # fallback kalau masih ada di user_data
            files = context.user_data.get("vcf_files", [])

        if not files:
            await query.edit_message_text("❌ Tidak ada file untuk diproses.", parse_mode="Markdown")
            return

        ok_count = 0
        for f in files:
            txt = self._vcf_to_txt(f["content"])
            bio = io.BytesIO(txt.encode("utf-8"))
            bio.name = f["filename"].replace(".vcf", ".txt")
            await query.message.reply_document(InputFile(bio))
            ok_count += 1
            await asyncio.sleep(SLEEP_BETWEEN_FILES)

        summary = "\n".join([
            "✅ *Konversi selesai!*",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            f"📁 *File diproses:* {ok_count}",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            "Gunakan /start untuk kembali ke menu utama.",
        ])
        await query.message.reply_text(summary, parse_mode="Markdown")

    async def _ask_merge_filename(self, query, context):
        context.user_data["waiting_for_merge_filename"] = True
        # pastikan kita tahu session message id
        context.user_data["vcf_session_msg_id"] = query.message.message_id
        await query.edit_message_text(
            "📝 Ketik nama file TXT gabungan:",
            parse_mode="Markdown"
        )

    async def handle_text_input(self, update, context):
        if not context.user_data.get("waiting_for_merge_filename"):
            return

        fname = (update.message.text or "").strip()
        if not fname:
            await update.message.reply_text("❌ Nama file tidak boleh kosong.")
            return
        if not fname.lower().endswith(".txt"):
            fname += ".txt"

        # Ambil files dari sesi per-pesan (lebih andal)
        sessions = context.chat_data.get(SESSION_BUCKET, {})
        msg_id = context.user_data.get("vcf_session_msg_id")
        session = sessions.get(msg_id, {})
        files = session.get("files") or context.user_data.get("vcf_files", [])

        merged_lines = []
        for f in files:
            txt = self._vcf_to_txt(f["content"])
            merged_lines.extend(txt.splitlines())

        out_txt = "\n".join(merged_lines)
        bio = io.BytesIO(out_txt.encode("utf-8"))
        bio.name = fname
        await update.message.reply_document(InputFile(bio))

        await update.message.reply_text(
            "✅ *Konversi selesai!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 *File gabungan:* {fname}\n"
            f"📄 *Total nomor:* {len(merged_lines)}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Gunakan /start untuk kembali ke menu utama.",
            parse_mode="Markdown"
        )

        # Bersihkan state terkait (termasuk sesi pesan)
        for k in ["vcf_files", "waiting_for_merge_filename", "vcf_preview_msg", "vcf_finalize_task", "vcf_session_msg_id"]:
            context.user_data.pop(k, None)
        if msg_id in sessions:
            sessions.pop(msg_id, None)

    # =========================
    # Helpers
    # =========================
    @staticmethod
    def _count_tel(content: str) -> int:
        n = 0
        for ln in (content or "").splitlines():
            if ln.strip().upper().startswith("TEL") and ":" in ln:
                right = ln.split(":", 1)[1].strip()
                if right:
                    n += 1
        return n

    @staticmethod
    def _vcf_to_txt(content: str) -> str:
        out = []
        for ln in (content or "").splitlines():
            up = ln.strip().upper()
            if up.startswith("TEL"):
                parts = ln.split(":", 1)
                if len(parts) == 2:
                    out.append(parts[1].strip())
        return "\n".join(out)
