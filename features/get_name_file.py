# features/get_name_file.py
import os
import asyncio
import time
from telegram.error import BadRequest
from config import UPLOAD_TIMEOUT

def _basename_no_ext(fname: str) -> str:
    base = os.path.basename(fname or "").strip()
    name, _ = os.path.splitext(base)
    return name or base or "tanpa_nama"

class GetNameFileHandler:
    """
    GET NAME FILE (UX baru):
      - Satu pesan "Ringkasan Upload" yang di-edit live saat file masuk
      - Menampilkan daftar nama (tanpa ekstensi) + status: membaca -> selesai
      - Setelah idle UPLOAD_TIMEOUT, kirim PESAN BARU berisi daftar final (tanpa penomoran)
      - Tidak mengunduh file; hanya baca document.file_name
    """

    async def start_mode(self, query, context):
        # state khusus fitur ini saja (jangan clear semua)
        context.user_data.update({
            "waiting_for_getname_files": True,
            "getname_names": [],                 # list[str] unik, urut upload
            "getname_last_ts": 0.0,
            "getname_finalize_task": None,
            "getname_preview_msg_id": None,     # pesan ringkasan yang di-edit
            "getname_chat_id": None,
        })

        text = (
            "📄 *GET NAME FILE*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Kirim beberapa file (.vcf, .txt, atau lainnya)."
        )
        try:
            await query.edit_message_text(text, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(text, parse_mode="Markdown")

    # =========================
    # Terima dokumen
    # =========================
    async def handle_document(self, update, context):
        if not context.user_data.get("waiting_for_getname_files"):
            return
        doc = update.message.document
        if not doc:
            return

        names = context.user_data.setdefault("getname_names", [])
        nm = _basename_no_ext(doc.file_name or "")
        if nm not in names:
            names.append(nm)
        context.user_data["getname_last_ts"] = time.time()

        # tampilkan / perbarui ringkasan
        await self._show_preview(update, context, final=False)
        # debounce finalize
        self._schedule_finalize(update, context)

    # =========================
    # Builder & Preview
    # =========================
    def _build_preview_text(self, names: list[str], final: bool) -> str:
        file_count = len(names)
        header = "📤 *Ringkasan Upload*\n━━━━━━━━━━━━━━━━━━━━━━━\n"
        body = "📁 *Daftar Nama (tanpa ekstensi):*\n"

        # tampilkan sampai 30 terakhir agar ringkas
        display_limit = 30
        display = names[-display_limit:] if file_count > display_limit else names
        body += "\n".join(f"- {n}" for n in display) + "\n\n"

        status = "✅ *Selesai membaca semua file.*\n\n" if final else "🔄 *Tunggu sebentar, bot sedang membaca file…*\n\n"
        footer = "━━━━━━━━━━━━━━━━━━━━━━━\n" \
                 f"🧮 *Total:* {file_count} file"

        return header + body + status + footer

    async def _show_preview(self, update, context, final: bool):
        names = context.user_data.get("getname_names", [])
        if not names:
            return

        text = self._build_preview_text(names, final)

        msg_id_key = "getname_preview_msg_id"
        chat_id_key = "getname_chat_id"
        msg_id = context.user_data.get(msg_id_key)
        chat_id = context.user_data.get(chat_id_key)

        if msg_id and chat_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    parse_mode="Markdown",
                )
            except BadRequest as e:
                if "message is not modified" not in str(e).lower():
                    sent = await update.message.reply_text(text, parse_mode="Markdown")
                    context.user_data[msg_id_key] = sent.message_id
                    context.user_data[chat_id_key] = sent.chat_id
            except Exception:
                sent = await update.message.reply_text(text, parse_mode="Markdown")
                context.user_data[msg_id_key] = sent.message_id
                context.user_data[chat_id_key] = sent.chat_id
        else:
            sent = await update.message.reply_text(text, parse_mode="Markdown")
            context.user_data[msg_id_key] = sent.message_id
            context.user_data[chat_id_key] = sent.chat_id

    # =========================
    # Debounce finalize
    # =========================
    def _schedule_finalize(self, update, context):
        old = context.user_data.get("getname_finalize_task")
        if old and not old.done():
            old.cancel()

        async def waiter():
            try:
                await asyncio.sleep(UPLOAD_TIMEOUT)
                last = context.user_data.get("getname_last_ts", 0.0)
                if time.time() - last >= UPLOAD_TIMEOUT - 0.05:
                    await self._finalize(update, context)
            except asyncio.CancelledError:
                pass

        context.user_data["getname_finalize_task"] = asyncio.create_task(waiter())

    async def _finalize(self, update, context):
        names = context.user_data.get("getname_names", [])
        if not names:
            return

        # stop menerima upload & edit ringkasan menjadi final
        context.user_data["waiting_for_getname_files"] = False
        await self._show_preview(update, context, final=True)

        # kirim PESAN BARU berisi daftar final (tanpa penomoran) dalam code block
        body = "\n".join(names)
        text = (
            f"✅ *Daftar nama file* ({len(names)} file):\n"
            "```\n"
            f"{body}\n"
            "```\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Ketik */start* untuk kembali ke menu utama."
        )
        await update.message.reply_text(text, parse_mode="Markdown")

        # bersihkan state fitur saja
        for key in [
            "waiting_for_getname_files", "getname_names",
            "getname_preview_msg_id", "getname_chat_id",
        ]:
            context.user_data.pop(key, None)
        task = context.user_data.pop("getname_finalize_task", None)
        if task and not task.done():
            task.cancel()
