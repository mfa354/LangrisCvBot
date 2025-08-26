# features/merge_files.py
import io
import time
import asyncio
from telegram import InputFile
from telegram.error import BadRequest
from config import UPLOAD_TIMEOUT, SLEEP_BETWEEN_FILES

SESSION_BUCKET = "merge_files_sessions"  # simpan sesi per pesan di chat_data


class MergeFilesHandler:
    """
    Merge TXT/VCF
    - 1 pesan "Ringkasan Upload" yang terus di-edit:
      status: sedang membaca -> selesai.
    - Setelah selesai, bot MENGIRIM PESAN BARU untuk meminta nama file output.
    - Data file disalin ke chat_data[SESSION_BUCKET][message_id] agar aman.
    """

    # =========================
    # Entry dari tombol
    # =========================
    async def handle_callback(self, query, context):
        if query.data in ("merge_txt", "merge_vcf"):
            ftype = "txt" if query.data == "merge_txt" else "vcf"
            # reset state khusus mode
            context.user_data.clear()
            context.user_data.update({
                f"waiting_for_merge_{ftype}_files": True,
                f"merge_{ftype}_files": [],            # list[{"filename","content","count"}]
                f"merge_{ftype}_last_ts": 0.0,
                f"merge_{ftype}_finalize_task": None,
                f"waiting_for_merge_{ftype}_filename": False,

                # id pesan ringkasan yang akan di-edit
                f"merge_{ftype}_preview_msg_id": None,
                f"merge_{ftype}_chat_id": None,

                # untuk handle input nama
                f"merge_{ftype}_session_msg_id": None,
            })
            await query.edit_message_text(
                f"📂 Upload file *.{ftype}* yang ingin digabung.\n"
                "Kamu bisa upload lebih dari satu.",
                parse_mode="Markdown"
            )

    # =========================
    # Terima file
    # =========================
    async def handle_document(self, update, context, ftype):
        if ftype == "txt" and not context.user_data.get("waiting_for_merge_txt_files"):
            return
        if ftype == "vcf" and not context.user_data.get("waiting_for_merge_vcf_files"):
            return

        doc = update.message.document
        if not doc:
            return
        if ftype == "txt" and not str(doc.file_name).lower().endswith(".txt"):
            await update.message.reply_text("❌ Hanya menerima file .txt"); return
        if ftype == "vcf" and not str(doc.file_name).lower().endswith(".vcf"):
            await update.message.reply_text("❌ Hanya menerima file .vcf"); return

        try:
            tg = await context.bot.get_file(doc.file_id)
            data = await tg.download_as_bytearray()
            content = data.decode("utf-8", errors="ignore")
        except Exception:
            await update.message.reply_text(f"❌ Gagal membaca `{doc.file_name}`", parse_mode="Markdown")
            return

        # hitung jumlah baris non-kosong (netral untuk TXT/VCF)
        count = self._count_lines(content)

        files_key = f"merge_{ftype}_files"
        last_ts_key = f"merge_{ftype}_last_ts"
        context.user_data[files_key].append({
            "filename": doc.file_name,
            "content": content,
            "count": count
        })
        context.user_data[last_ts_key] = time.time()

        # update / kirim ringkasan live (tanpa instruksi ketik nama)
        await self._show_preview(update, context, ftype, final=False)

        # jadwalkan finalize dengan debounce idle
        self._schedule_finalize(update, context, ftype)

    # =========================
    # Preview builder
    # =========================
    def _build_status_line(self, final: bool) -> str:
        return "✅ *Selesai membaca semua file.*" if final else "🔄 *Tunggu sebentar, bot sedang membaca file…*"

    def _build_preview_text(self, files: list, final: bool, ftype: str) -> str:
        file_count = len(files)
        total_lines = sum(f["count"] for f in files)

        header = "📤 *Ringkasan Upload*\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        body = "📁 *Daftar File:*\n"

        display_limit = 15
        display_files = files[-display_limit:] if file_count > display_limit else files
        start_idx = file_count - len(display_files) + 1
        for idx, f in enumerate(display_files, start=start_idx):
            body += f"{idx}. `{f['filename']}` — 🧾 {f['count']} nomor\n"

        # final TIDAK menambahkan instruksi; instruksi dikirim sebagai PESAN BARU
        status = f"{self._build_status_line(final)}\n\n"

        footer = "━━━━━━━━━━━━━━━━━━━━━━━\n" \
                 f"🧮 *Total:* {file_count} file · {total_lines} nomor"

        return header + body + status + footer

    async def _show_preview(self, update, context, ftype: str, final: bool):
        files = context.user_data.get(f"merge_{ftype}_files", [])
        if not files:
            return

        text = self._build_preview_text(files, final, ftype)

        id_key   = f"merge_{ftype}_preview_msg_id"
        chat_key = f"merge_{ftype}_chat_id"
        msg_id   = context.user_data.get(id_key)
        chat_id  = context.user_data.get(chat_key)

        # sudah ada pesan ringkasan -> edit
        if msg_id and chat_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    parse_mode="Markdown",
                )
            except BadRequest as e:
                s = str(e).lower()
                if "message is not modified" in s:
                    pass
                else:
                    sent = await update.message.reply_text(text, parse_mode="Markdown")
                    context.user_data[id_key] = sent.message_id
                    context.user_data[chat_key] = sent.chat_id
                    msg_id = sent.message_id
                    chat_id = sent.chat_id
            except Exception:
                sent = await update.message.reply_text(text, parse_mode="Markdown")
                context.user_data[id_key] = sent.message_id
                context.user_data[chat_key] = sent.chat_id
                msg_id = sent.message_id
                chat_id = sent.chat_id
        else:
            # belum ada -> kirim pertama kali
            sent = await update.message.reply_text(text, parse_mode="Markdown")
            context.user_data[id_key] = sent.message_id
            context.user_data[chat_key] = sent.chat_id
            msg_id = sent.message_id
            chat_id = sent.chat_id

        # === simpan sesi per pesan di chat_data ===
        bucket = context.chat_data.setdefault(SESSION_BUCKET, {})
        bucket[msg_id] = {
            "ftype": ftype,
            "files": [dict(f) for f in files],  # shallow copy agar aman
            "ts": time.time(),
        }
        # simpan msg_id untuk dipakai saat user mengetik nama file
        context.user_data[f"merge_{ftype}_session_msg_id"] = msg_id

    # =========================
    # Debounce finalize
    # =========================
    def _schedule_finalize(self, update, context, ftype):
        task_key = f"merge_{ftype}_finalize_task"
        old = context.user_data.get(task_key)
        if old and not old.done():
            old.cancel()

        async def waiter():
            try:
                await asyncio.sleep(UPLOAD_TIMEOUT)
                last = context.user_data.get(f"merge_{ftype}_last_ts", 0.0)
                if time.time() - last >= UPLOAD_TIMEOUT - 0.05:
                    await self._finalize(update, context, ftype)
            except asyncio.CancelledError:
                pass

        context.user_data[task_key] = asyncio.create_task(waiter())

    async def _finalize(self, update, context, ftype):
        files_key = f"merge_{ftype}_files"
        if not context.user_data.get(files_key):
            return

        # stop menerima upload & tampilkan PREVIEW FINAL
        context.user_data[f"waiting_for_merge_{ftype}_files"] = False
        await self._show_preview(update, context, ftype, final=True)

        # lalu KIRIM PESAN BARU untuk input nama file
        context.user_data[f"waiting_for_merge_{ftype}_filename"] = True
        chat_id = context.user_data.get(f"merge_{ftype}_chat_id") or update.effective_chat.id
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📝 *Ketik nama file* (.{ftype}) di bawah ini.",
            parse_mode="Markdown"
        )

        task = context.user_data.pop(f"merge_{ftype}_finalize_task", None)
        if task and not task.done():
            task.cancel()

    # =========================
    # Tangkap nama file output
    # =========================
    async def handle_text_input(self, update, context):
        if context.user_data.get("waiting_for_merge_txt_filename"):
            await self._merge_and_send(update, context, "txt")
        elif context.user_data.get("waiting_for_merge_vcf_filename"):
            await self._merge_and_send(update, context, "vcf")

    async def _merge_and_send(self, update, context, ftype):
        fname = (update.message.text or "").strip()
        if not fname:
            await update.message.reply_text("❌ Nama file tidak boleh kosong."); return
        if not fname.lower().endswith(f".{ftype}"):
            fname += f".{ftype}"

        # Ambil dari sesi per-pesan (andal), fallback: user_data
        sessions = context.chat_data.get(SESSION_BUCKET, {})
        msg_id = context.user_data.get(f"merge_{ftype}_session_msg_id")
        session = sessions.get(msg_id, {})
        files = session.get("files") or context.user_data.get(f"merge_{ftype}_files", [])

        if not files:
            await update.message.reply_text("❌ Tidak ada file untuk digabung.")
            return

        merged_lines = []
        for f in files:
            merged_lines.extend((f["content"] or "").splitlines())

        # hapus duplikat tapi pertahankan urutan
        merged_lines = list(dict.fromkeys(ln for ln in merged_lines))

        out_txt = "\n".join(merged_lines)
        bio = io.BytesIO(out_txt.encode("utf-8"))
        bio.name = fname
        await update.message.reply_document(InputFile(bio))

        await update.message.reply_text(
            "✅ *Merge selesai!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 *File gabungan:* {fname}\n"
            f"📄 *Total nomor unik:* {len(merged_lines)}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Gunakan /start untuk kembali ke menu utama.",
            parse_mode="Markdown"
        )

        # clear state + sesi
        for k in [
            f"merge_{ftype}_files",
            f"waiting_for_merge_{ftype}_filename",
            f"merge_{ftype}_finalize_task",
            f"merge_{ftype}_session_msg_id",
            f"waiting_for_merge_{ftype}_files",
            f"merge_{ftype}_last_ts",
            f"merge_{ftype}_preview_msg_id",
            f"merge_{ftype}_chat_id",
        ]:
            context.user_data.pop(k, None)
        if msg_id in sessions:
            sessions.pop(msg_id, None)

    # =========================
    # Helpers
    # =========================
    @staticmethod
    def _count_lines(content: str) -> int:
        """Jumlah baris non-kosong untuk ringkasan."""
        return sum(1 for ln in (content or "").splitlines() if ln.strip())
