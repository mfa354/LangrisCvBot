# features/split_files.py
import io
import time
import asyncio
import math
import re
from telegram import InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from config import UPLOAD_TIMEOUT, SLEEP_BETWEEN_FILES
from utils import read_file_content, clean_phone_number, normalize_phone

class SplitFilesHandler:
    """
    SPLIT TXT/VCF
    """

    async def start_mode(self, query, context):
        context.user_data.clear()
        context.user_data.update({
            "waiting_for_split_files": True,
            "split_files": [],  
            "split_last_ts": 0.0,
            "split_finalize_task": None,
            "waiting_for_split_count": False,
            "waiting_for_split_name": False,
            "split_target_count": 0,
            "split_preview_msg": None,
        })
        await query.edit_message_text(
            "✂️ *SPLIT TXT/VCF*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📂 Upload 1 file *.txt* atau *.vcf*.",
            parse_mode="Markdown"
        )

    async def handle_document(self, update, context):
        if not context.user_data.get("waiting_for_split_files"):
            return
        doc = update.message.document
        if not doc:
            return
        fname = (doc.file_name or "").lower()
        if not (fname.endswith(".txt") or fname.endswith(".vcf")):
            await update.message.reply_text("❌ Hanya menerima file .txt atau .vcf")
            return

        tg_file = await context.bot.get_file(doc.file_id)
        data = await tg_file.download_as_bytearray()
        text = read_file_content(data)
        if not text:
            await update.message.reply_text("❌ Tidak bisa membaca file.")
            return

        if fname.endswith(".txt"):
            items = [ln.strip() for ln in text.splitlines() if ln.strip()]
            ftype = "txt"
        else:
            items = []
            vcards = re.findall(r'BEGIN:VCARD.*?END:VCARD', text, re.DOTALL | re.IGNORECASE)
            for v in vcards:
                for m in re.findall(r'TEL[^:]*:([^\r\n]+)', v, re.IGNORECASE):
                    cleaned = clean_phone_number(m)
                    if cleaned:
                        items.append(normalize_phone(cleaned))
            ftype = "vcf"

        context.user_data["split_files"] = [{
            "filename": doc.file_name,
            "type": ftype,
            "items": items,
        }]
        context.user_data["split_last_ts"] = time.time()

        await self._show_preview(update, context, final=False)
        self._schedule_finalize(update, context)

    def _build_preview_text(self, files, final: bool) -> str:
        total = sum(len(f["items"]) for f in files)
        fname = files[0]["filename"] if files else "-"
        status = "✅ *Selesai membaca file.*" if final else "🔄 *Sedang membaca file…*"
        return (
            "📤 *Ringkasan Upload*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 File: `{fname}`\n"
            f"📄 Total: {total} nomor\n\n"
            f"{status}"
        )

    async def _show_preview(self, update, context, final: bool):
        files = context.user_data.get("split_files", [])
        if not files:
            return
        text = self._build_preview_text(files, final)
        msg = context.user_data.get("split_preview_msg")

        if msg:
            try:
                await msg.edit_text(text, parse_mode="Markdown")
            except Exception:
                msg = await update.message.reply_text(text, parse_mode="Markdown")
                context.user_data["split_preview_msg"] = msg
        else:
            msg = await update.message.reply_text(text, parse_mode="Markdown")
            context.user_data["split_preview_msg"] = msg

    def _schedule_finalize(self, update, context):
        old = context.user_data.get("split_finalize_task")
        if old and not old.done():
            old.cancel()

        async def waiter():
            await asyncio.sleep(UPLOAD_TIMEOUT)
            last = context.user_data.get("split_last_ts", 0.0)
            if time.time() - last >= UPLOAD_TIMEOUT - 0.05:
                await self._finalize(update, context)

        context.user_data["split_finalize_task"] = asyncio.create_task(waiter())

    async def _finalize(self, update, context):
        files = context.user_data.get("split_files", [])
        if not files:
            return
        context.user_data["waiting_for_split_files"] = False
        context.user_data["waiting_for_split_count"] = True

        await self._show_preview(update, context, final=True)

        total = sum(len(f["items"]) for f in files)
        await update.message.reply_text(
            f"🧮 Masukkan jumlah file untuk split:\n"
            f"📄 Total nomor: {total}",
            parse_mode="Markdown"
        )

    async def handle_text_input(self, update, context):
        if context.user_data.get("waiting_for_split_count"):
            raw = (update.message.text or "").strip()
            try:
                n = int(raw)
                if n <= 0:
                    raise ValueError()
            except Exception:
                await update.message.reply_text("❌ Masukkan angka valid (>0).")
                return
            context.user_data["split_target_count"] = n
            context.user_data["waiting_for_split_count"] = False

            total = sum(len(f["items"]) for f in context.user_data["split_files"])
            perfile = math.ceil(total / n)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📝 Custom Name", callback_data="split_custom"),
                InlineKeyboardButton("✅ Selesai", callback_data="split_done")
            ]])
            await update.message.reply_text(
                f"✅ Siap split {total} nomor menjadi {n} file\n"
                f"≈ {perfile} nomor per file.\n\n"
                "Pilih opsi:",
                parse_mode="Markdown",
                reply_markup=kb
            )
            return

        if context.user_data.get("waiting_for_split_name"):
            base = (update.message.text or "").strip()
            if not base or not any(ch.isdigit() for ch in base):
                await update.message.reply_text("❌ Nama dasar harus diakhiri angka.")
                return
            await self._do_split(update, context, base_name=base)
            return

    async def handle_callback(self, query, context):
        if query.data == "split_done":
            await self._do_split(query, context, base_name=None)
        elif query.data == "split_custom":
            context.user_data["waiting_for_split_name"] = True
            await query.edit_message_text(
                "📝 Ketik *nama dasar* file (wajib diakhiri angka).\n"
                "Contoh: `kontak1`, `data-5`",
                parse_mode="Markdown"
            )

    async def _do_split(self, target, context, base_name: str | None):
        files = context.user_data.get("split_files", [])
        if not files:
            return
        n = context.user_data.get("split_target_count", 0)
        if n <= 0:
            return

        items = files[0]["items"]
        ftype = files[0]["type"]
        fname = files[0]["filename"].rsplit(".", 1)[0]

        batch_size = math.ceil(len(items) / n)
        sent_files = 0
        total_out = 0
        start_time = time.time()

        progress_msg = await (target.message.reply_text("🔄 Memproses split…") if hasattr(target, "message") else target.reply_text("🔄 Memproses split…"))

        for i in range(n):
            part = items[i*batch_size:(i+1)*batch_size]
            if not part:
                continue

            # nama file
            if base_name:  
                m = re.search(r'(.+?)(\d+)$', base_name)
                if m:
                    prefix, startnum = m.group(1), int(m.group(2))
                    outname = f"{prefix}{startnum + i}.{ftype}"
                else:
                    outname = f"{base_name}{i+1}.{ftype}"
            else:
                outname = f"{fname}_{i+1}.{ftype}"

            # isi file
            if ftype == "txt":
                content = "\n".join(part)
            else:
                content = ""
                for ph in part:
                    total_out += 1
                    content += (
                        "BEGIN:VCARD\nVERSION:3.0\n"
                        f"FN:Kontak {total_out}\nTEL:{ph}\nEND:VCARD\n"
                    )

            bio = io.BytesIO(content.encode("utf-8"))
            bio.name = outname
            msg_target = target.message if hasattr(target, "message") else target
            await msg_target.reply_document(InputFile(bio))
            sent_files += 1

            pct = int((sent_files / n) * 100)
            try:
                await progress_msg.edit_text(f"📤 Output {sent_files}/{n} file ({pct}%)…")
            except Exception:
                pass
            await asyncio.sleep(SLEEP_BETWEEN_FILES)

        dur = time.time() - start_time
        # tutup progress
        try:
            await progress_msg.edit_text("✅ Semua file sudah terkirim.")
        except Exception:
            pass

        # kirim ringkasan baru
        summary = (
            "📊 *Ringkasan SPLIT*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 File berhasil: {sent_files}\n"
            f"📄 Total nomor: {len(items)}\n"
            f"⏱ Waktu proses: {dur:.2f} detik\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Gunakan /start untuk kembali ke menu utama."
        )
        msg_target = target.message if hasattr(target, "message") else target
        await msg_target.reply_text(summary, parse_mode="Markdown")

        context.user_data.clear()
