# features/edit_ctc_name.py
import asyncio
import io
import time
from telegram import InputFile
from telegram.error import BadRequest
from config import UPLOAD_TIMEOUT, SLEEP_BETWEEN_FILES

SESSION_BUCKET = "edit_ctc_sessions"  # simpan sesi per-pesan di chat_data


class EditCtcNameHandler:
    """
    EDIT CTC NAME (UX baru – Ringkasan Upload, debounce & 1 output final)
    Flow:
      1) start_mode  -> minta upload beberapa VCF
      2) handle_document -> simpan file; tampilkan "Ringkasan Upload" yang live
      3) Idle UPLOAD_TIMEOUT -> pesan ringkasan di-edit jadi 'Selesai', lalu
         bot KIRIM PESAN BARU minta *nama kontak dasar*
      4) handle_text -> ubah FN: setiap vcard & kirim ulang file dg nama asli,
         lalu kirim ringkasan.
    """

    # ========= Helpers VCF =========
    @staticmethod
    def _parse_blocks(content: str):
        blocks, cur = [], []
        for line in (content or "").splitlines():
            up = line.strip().upper()
            if up == "BEGIN:VCARD":
                cur = [line]
            elif up == "END:VCARD":
                cur.append(line)
                blocks.append(cur[:])
                cur = []
            elif cur:
                cur.append(line)
        return blocks

    @staticmethod
    def _rename_blocks(blocks, base_name: str):
        """
        Ganti/selipkan FN: <base_name> <i>
        - Jika FN: ada → ganti pertama saja
        - Jika FN: tidak ada → selipkan setelah VERSION:
        """
        out, i = [], 1
        for b in blocks:
            nb, replaced = [], False
            for ln in b:
                up = ln.upper()
                if up.startswith("FN:") and not replaced:
                    nb.append(f"FN:{base_name} {i}")
                    replaced = True
                else:
                    nb.append(ln)
            if not replaced:
                nb2, inserted = [], False
                for ln in nb:
                    nb2.append(ln)
                    if not inserted and ln.upper().startswith("VERSION:"):
                        nb2.append(f"FN:{base_name} {i}")
                        inserted = True
                nb = nb2
            out.append(nb)
            i += 1
        return out

    @staticmethod
    def _dump(blocks):
        out = []
        for b in blocks:
            out.extend(b)
            if b and b[-1].strip().upper() != "END:VCARD":
                out.append("END:VCARD")
            out.append("")  # pemisah antar vcard
        return "\n".join(out).strip() + "\n"

    # ========= Builder Ringkasan =========
    def _build_status_line(self, final: bool) -> str:
        return "✅ *Selesai membaca semua file.*" if final else "🔄 *Tunggu sebentar, bot sedang membaca file…*"

    def _build_preview_text(self, files: list, final: bool) -> str:
        file_count = len(files)
        total_contacts = sum(f["contacts"] for f in files)

        header = "📤 *Ringkasan Upload*\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        body = "📁 *Daftar File:*\n"

        display_limit = 15
        display_files = files[-display_limit:] if file_count > display_limit else files
        start_idx = file_count - len(display_files) + 1
        for idx, f in enumerate(display_files, start=start_idx):
            body += f"{idx}. `{f['filename']}` — 👤 {f['contacts']} kontak\n"

        status = f"{self._build_status_line(final)}\n\n"
        footer = "━━━━━━━━━━━━━━━━━━━━━━━\n" \
                 f"🧮 *Total:* {file_count} file · {total_contacts} kontak"

        return header + body + status + footer

    async def _show_preview(self, update, context, final: bool):
        files = context.user_data.get("edit_files_dict", [])
        if not files:
            return

        text = self._build_preview_text(files, final)

        id_key, chat_key = "edit_preview_msg_id", "edit_chat_id"
        msg_id = context.user_data.get(id_key)
        chat_id = context.user_data.get(chat_key)

        # edit jika sudah ada, kalau gagal baru kirim baru
        if msg_id and chat_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id, text=text, parse_mode="Markdown"
                )
            except BadRequest as e:
                if "message is not modified" not in str(e).lower():
                    sent = await update.message.reply_text(text, parse_mode="Markdown")
                    context.user_data[id_key] = sent.message_id
                    context.user_data[chat_key] = sent.chat_id
                    msg_id, chat_id = sent.message_id, sent.chat_id
        else:
            sent = await update.message.reply_text(text, parse_mode="Markdown")
            context.user_data[id_key] = sent.message_id
            context.user_data[chat_key] = sent.chat_id
            msg_id, chat_id = sent.message_id, sent.chat_id

        # simpan sesi per-pesan (agar aman kalau user_data ke-reset)
        bucket = context.chat_data.setdefault(SESSION_BUCKET, {})
        bucket[msg_id] = {
            "files": [dict(f) for f in files],  # shallow copy
            "ts": time.time(),
        }
        context.user_data["edit_session_msg_id"] = msg_id

    # ========= Entry =========
    async def start_mode(self, query, context):
        context.user_data.clear()
        context.user_data.update({
            "waiting_for_edit_vcf_files": True,
            # simpan juga bentuk dict utk ringkasan (filename, contacts)
            "edit_files": [],          # [(filename, raw_text)]
            "edit_files_dict": [],     # [{"filename","text","contacts"}]
            "edit_last_ts": 0.0,
            "edit_preview_msg_id": None,
            "edit_chat_id": None,
            "edit_finalize_task": None,
            "waiting_for_edit_name": False,
            "edit_session_msg_id": None,
        })

        text = (
            "✏️ *EDIT CTC NAME*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📎 Upload satu atau beberapa file *.vcf*.\n"
        )
        await query.edit_message_text(text, parse_mode="Markdown")

    # ========= Upload handler =========
    async def handle_document(self, update, context):
        if not context.user_data.get("waiting_for_edit_vcf_files"):
            return

        doc = update.message.document
        if not doc or not str(doc.file_name).lower().endswith(".vcf"):
            await update.message.reply_text("❌ Hanya menerima file .vcf")
            return

        try:
            tg = await context.bot.get_file(doc.file_id)
            data = await tg.download_as_bytearray()
            raw = data.decode("utf-8", errors="ignore")
        except Exception:
            await update.message.reply_text(f"❌ Gagal membaca `{doc.file_name}`", parse_mode="Markdown")
            return

        # hitung kontak = jumlah blok vcard
        blocks = self._parse_blocks(raw)
        contacts = len(blocks)

        # simpan
        context.user_data["edit_files"].append((doc.file_name, raw))
        context.user_data["edit_files_dict"].append({
            "filename": doc.file_name,
            "text": raw,
            "contacts": contacts
        })
        context.user_data["edit_last_ts"] = time.time()

        # ringkasan live
        await self._show_preview(update, context, final=False)

        # debounce finalize
        self._schedule_finalize(update, context)

    # ========= Debounce finalize =========
    def _schedule_finalize(self, update, context):
        old = context.user_data.get("edit_finalize_task")
        if old and not old.done():
            old.cancel()

        async def waiter():
            try:
                await asyncio.sleep(UPLOAD_TIMEOUT)
                last = context.user_data.get("edit_last_ts", 0.0)
                if time.time() - last >= UPLOAD_TIMEOUT - 0.05:
                    await self._finalize(update, context)
            except asyncio.CancelledError:
                pass

        context.user_data["edit_finalize_task"] = asyncio.create_task(waiter())

    async def _finalize(self, update, context):
        if not context.user_data.get("edit_files_dict"):
            return

        # stop menerima upload & tampilkan PREVIEW FINAL
        context.user_data["waiting_for_edit_vcf_files"] = False
        await self._show_preview(update, context, final=True)

        # kirim PESAN BARU untuk minta nama kontak dasar
        context.user_data["waiting_for_edit_name"] = True
        chat_id = context.user_data.get("edit_chat_id") or update.effective_chat.id
        await context.bot.send_message(
            chat_id=chat_id,
            text="📝 *Ketik nama dasar kontak*.\n_Contoh: Admin, HHD, Kontak Baru_",
            parse_mode="Markdown"
        )

        task = context.user_data.pop("edit_finalize_task", None)
        if task and not task.done():
            task.cancel()

    # ========= Text handler (apply name) =========
    async def handle_text(self, update, context):
        if not context.user_data.get("waiting_for_edit_name"):
            return

        new_name = (update.message.text or "").strip()
        if not new_name:
            await update.message.reply_text("❌ Nama kosong. Ketik nama kontak baru.")
            return

        # ambil files dari sesi per-pesan (andal), fallback ke user_data
        sessions = context.chat_data.get(SESSION_BUCKET, {})
        msg_id = context.user_data.get("edit_session_msg_id")
        session = sessions.get(msg_id, {})
        files_dict = session.get("files") or context.user_data.get("edit_files_dict", [])

        ok_count, total_contacts = 0, sum(f["contacts"] for f in files_dict)

        for f in files_dict:
            blocks = self._parse_blocks(f["text"])
            renamed = self._rename_blocks(blocks, new_name)
            out_txt = self._dump(renamed)

            bio = io.BytesIO(out_txt.encode("utf-8"))
            bio.name = f["filename"]  # nama file asli
            await update.message.reply_document(InputFile(bio))
            ok_count += 1
            await asyncio.sleep(SLEEP_BETWEEN_FILES)

        # ringkasan akhir
        summary = "\n".join([
            "✅ *EDIT CTC NAME selesai!*",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            f"📁 *File diproses:* {ok_count}",
            f"👤 *Total kontak:* {total_contacts}",
            "━━━━━━━━━━━━━━━━━━━━━━━",
            "Ketik */start* untuk kembali ke menu utama."
        ])
        await update.message.reply_text(summary, parse_mode="Markdown")

        # bersihkan state + sesi
        for k in [
            "waiting_for_edit_vcf_files", "waiting_for_edit_name",
            "edit_files", "edit_files_dict", "edit_preview_msg_id",
            "edit_chat_id", "edit_finalize_task", "edit_last_ts",
            "edit_session_msg_id",
        ]:
            context.user_data.pop(k, None)
        if msg_id in sessions:
            sessions.pop(msg_id, None)
