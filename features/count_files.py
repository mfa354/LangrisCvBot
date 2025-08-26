# features/count_files.py
import asyncio
import time
import logging
import contextlib
from config import UPLOAD_TIMEOUT
from utils import read_file_content, parse_vcf_content

logger = logging.getLogger(__name__)

class CountFilesHandler:
    """
    COUNT VCF/TXT — versi ringkas
    - Start: tampilkan instruksi singkat.
    - Saat file pertama diterima: kirim 1 pesan "🔄 Sedang membaca file…".
    - Tidak ada progres per-file.
    - Setelah idle (UPLOAD_TIMEOUT): hapus/ubah pesan tunggu → kirim ringkasan
      berisi daftar nama file + jumlah, lalu total di bawahnya.
    """

    async def start_mode(self, query, context):
        try:
            context.user_data.clear()
            context.user_data.update({
                'waiting_for_count_files': True,
                'count_items': [],          # [{'filename','type','count'}]
                'last_upload_time': 0.0,
                'waiting_msg': None,        # Message "Sedang membaca…"
            })
            await query.edit_message_text(
                "🔢 *COUNT VCF/TXT*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📤 Upload satu/beberapa file `.txt` atau `.vcf`",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"[CountFiles] start_mode error: {e}")

    async def handle_document(self, update, context):
        if not context.user_data.get('waiting_for_count_files'):
            return

        doc = update.message.document
        fname = (doc.file_name or "").strip()
        lower = fname.lower()

        if not (lower.endswith('.txt') or lower.endswith('.vcf')):
            await update.message.reply_text("❌ Hanya menerima file .txt atau .vcf")
            return

        try:
            tg_file = await context.bot.get_file(doc.file_id)
            file_content = await tg_file.download_as_bytearray()
            text = read_file_content(file_content)
            if text is None:
                await update.message.reply_text(f"❌ Tidak bisa membaca `{fname}`", parse_mode='Markdown')
                return

            # Hitung isi file
            if lower.endswith('.txt'):
                count = sum(1 for ln in text.splitlines() if ln.strip())
                ftype = 'txt'
            else:
                count = len(parse_vcf_content(text))
                ftype = 'vcf'

            # Simpan
            context.user_data.setdefault('count_items', []).append(
                {'filename': fname, 'type': ftype, 'count': count}
            )
            context.user_data['last_upload_time'] = time.time()

            # Pesan tunggu (kirim sekali saja saat file pertama)
            if context.user_data.get('waiting_msg') is None:
                context.user_data['waiting_msg'] = await update.message.reply_text(
                    "🔄 Sedang membaca file…",
                    parse_mode='Markdown'
                )

            # Jadwalkan ringkasan akhir setelah idle
            asyncio.create_task(self._delayed_summary(update, context))

        except Exception as e:
            logger.error(f"[CountFiles] handle_document error: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan saat memproses file.")

    async def _delayed_summary(self, update, context):
        await asyncio.sleep(UPLOAD_TIMEOUT)
        await self._maybe_send_summary(update, context)

    async def _maybe_send_summary(self, update, context):
        try:
            # Belum idle? keluar.
            if time.time() - context.user_data.get('last_upload_time', 0) < UPLOAD_TIMEOUT:
                return

            items = context.user_data.get('count_items', [])
            if not items:
                return

            # Tutup pesan tunggu
            wmsg = context.user_data.get('waiting_msg')
            if wmsg:
                with contextlib.suppress(Exception):
                    await wmsg.edit_text("✅ Selesai membaca semua file.", parse_mode='Markdown')
            context.user_data['waiting_msg'] = None

            # Bangun ringkasan
            total_txt = sum(i['count'] for i in items if i['type'] == 'txt')
            total_vcf = sum(i['count'] for i in items if i['type'] == 'vcf')
            total_all = total_txt + total_vcf

            lines = ["📊 *RINGKASAN COUNT*", "━━━━━━━━━━━━━━━━━━━━━━━"]
            for it in items:
                unit = "nomor" if it['type'] == 'txt' else "kontak"
                lines.append(f"• `{it['filename']}` — {it['count']} {unit}")
            lines.extend([
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"TXT total: *{total_txt}* nomor",
                f"VCF total: *{total_vcf}* kontak",
                f"📊 TOTAL semua: *{total_all}*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "Gunakan /start untuk memulai baru."
            ])

            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
            context.user_data.clear()

        except Exception as e:
            logger.error(f"[CountFiles] summary error: {e}")
            with contextlib.suppress(Exception):
                await update.message.reply_text("❌ Gagal membuat ringkasan.")
            context.user_data.clear()
