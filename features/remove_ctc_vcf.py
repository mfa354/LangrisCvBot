# features/remove_ctc_vcf.py
from telegram import InputFile
import io, re

def _digits(s:str)->str: return re.sub(r"\D+","",s or "")

def _parse_vcards(text:str):
    blocks, block=[],[]
    for ln in text.splitlines():
        up=ln.strip().upper()
        if up=="BEGIN:VCARD": block=[ln]
        elif up=="END:VCARD":
            block.append(ln); blocks.append(block[:]); block=[]
        elif block: block.append(ln)
    return blocks

def _block_tels(block):
    out=[]
    for ln in block:
        if ln.upper().startswith("TEL"):
            part=ln.split(":",1)
            if len(part)==2: out.append(part[1].strip())
    return out

def _dump(blocks):
    out=[]
    for b in blocks:
        out.extend(b)
        if b[-1].strip().upper()!="END:VCARD": out.append("END:VCARD")
        out.append("")
    return "\n".join(out).strip()+"\n"

class RemoveCtcVcfHandler:
    """
    Flow:
      1) start_mode → minta 1 VCF
      2) handle_document (VCF) → simpan blocks, minta daftar nomor target (multi-baris)
      3) handle_text → hapus kontak yang mengandung nomor target (dibandingkan pakai digit saja)
      4) KIRIM FILE DULU (tanpa caption), LALU INFO/summary DI PESAN TERPISAH
    """

    async def start_mode(self, query, context):
        context.user_data.clear()
        context.user_data.update({
            "waiting_for_remove_vcf_file": True,
            "rem": {}
        })
        await query.edit_message_text(
            "📎 Upload *1 file VCF* untuk dihapus kontaknya.\n"
            "Lalu kirim *daftar nomor* (1 baris = 1 nomor).",
            parse_mode="Markdown"
        )

    async def handle_document(self, update, context):
        if not context.user_data.get("waiting_for_remove_vcf_file"):
            return
        doc = update.message.document
        if not doc or not doc.file_name.lower().endswith(".vcf"):
            await update.message.reply_text("❌ File harus .vcf"); return

        tg_file = await context.bot.get_file(doc.file_id)
        data = await tg_file.download_as_bytearray()
        text = data.decode("utf-8", errors="ignore")
        blocks = _parse_vcards(text)

        context.user_data["rem"] = {"blocks":blocks,"before":len(blocks),"fname":doc.file_name}
        context.user_data["waiting_for_remove_vcf_file"] = False
        context.user_data["waiting_for_phone_to_remove"] = True

        await update.message.reply_text(
            "✍️ Kirim *nomor telepon* yang ingin dihapus (multi-baris).\n"
            "Perbandingan memakai *angka saja* (spasi/simbol diabaikan).",
            parse_mode="Markdown"
        )

    async def handle_text(self, update, context):
        if not context.user_data.get("waiting_for_phone_to_remove"):
            return
        raw = update.message.text or ""
        targets = [_digits(s.strip()) for s in raw.splitlines() if _digits(s.strip())]
        if not targets:
            await update.message.reply_text("❌ Tidak ada nomor valid. Kirim lagi."); return

        data = context.user_data["rem"]
        blocks = data["blocks"]
        target = set(targets)
        kept=[]; removed=0
        for b in blocks:
            tels = [_digits(t) for t in _block_tels(b)]
            if any(t in target for t in tels): removed += 1
            else: kept.append(b)

        out = _dump(kept)
        before = data["before"]; after = len(kept)
        out_name = data["fname"]

        bio=io.BytesIO(out.encode("utf-8")); bio.name=out_name

        # 1) Kirim FILE terlebih dahulu (TANPA caption)
        await update.message.reply_document(InputFile(bio))

        # 2) Kirim INFO/summary sebagai pesan teks terpisah
        info = (
            "🗑️ *REMOVE CTC VCF selesai*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📄 *File:* `{out_name}`\n"
            f"👥 *Sebelum:* **{before}** kontak\n"
            f"➖ *Dihapus:* **{removed}** kontak\n"
            f"👤 *Sesudah:* **{after}** kontak\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Ketik */start* untuk kembali ke menu utama."
        )
        await update.message.reply_text(info, parse_mode="Markdown")

        context.user_data.clear()
