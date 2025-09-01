# features/txt_vcf_to_text.py
import re
import time
from utils import read_file_content, clean_phone_number, normalize_phone
from config import UPLOAD_TIMEOUT

class TxtVcfToTextHandler:
    """
    TXT/VCF → TEXT
    - Upload .txt → tampilkan isi file (as-is)
    - Upload .vcf → tampilkan hanya nomor (1 baris per nomor)
    """

    async def start_mode(self, query, context):
        context.user_data.clear()
        context.user_data.update({
            "waiting_for_txt_vcf_to_text": True,
        })
        await query.edit_message_text(
            "📄 *TXT/VCF TO TEXT*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📂 Upload file *.txt* atau *.vcf*.",
            parse_mode="Markdown"
        )

    async def handle_document(self, update, context):
        if not context.user_data.get("waiting_for_txt_vcf_to_text"):
            return
        doc = update.message.document
        if not doc:
            return

        fname = (doc.file_name or "").lower()
        if not (fname.endswith(".txt") or fname.endswith(".vcf")):
            await update.message.reply_text("❌ Hanya menerima file .txt atau .vcf")
            return

        # tampilkan status membaca
        status_msg = await update.message.reply_text("🔄 Sedang membaca file…")

        tg_file = await context.bot.get_file(doc.file_id)
        data = await tg_file.download_as_bytearray()
        text = read_file_content(data)
        if not text:
            await status_msg.edit_text("❌ Tidak bisa membaca file.")
            return

        if fname.endswith(".txt"):
            lines = [ln for ln in text.splitlines() if ln.strip()]
            preview = "\n".join(lines)
            total = len(lines)
            tipe = "baris"
        else:
            numbers = []
            vcards = re.findall(r'BEGIN:VCARD.*?END:VCARD', text, re.DOTALL | re.IGNORECASE)
            for v in vcards:
                for m in re.findall(r'TEL[^:]*:([^\r\n]+)', v, re.IGNORECASE):
                    cleaned = clean_phone_number(m)
                    if cleaned:
                        numbers.append(normalize_phone(cleaned))
            preview = "\n".join(numbers)
            total = len(numbers)
            tipe = "nomor"

        if not preview:
            await status_msg.edit_text("❌ File kosong atau tidak ada nomor.")
            return

        # batasi panjang supaya aman
        if len(preview) > 4000:
            preview = preview[:4000] + "\n… (dipotong)"

        # update status jadi selesai
        try:
            await status_msg.edit_text("✅ Selesai membaca file.")
        except Exception:
            pass

        # kirim isi file
        await update.message.reply_text(
            f"✅ *Isi file {doc.file_name}:*\n"
            "```\n"
            f"{preview}\n"
            "```",
            parse_mode="Markdown"
        )

        # kirim ringkasan
        await update.message.reply_text(
            f"📊 *Ringkasan TXT/VCF → TEXT*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📂 File: `{doc.file_name}`\n"
            f"📄 Total {tipe}: {total}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Gunakan /start untuk kembali ke menu utama.",
            parse_mode="Markdown"
        )

        context.user_data.clear()
