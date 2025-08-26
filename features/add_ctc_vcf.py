# features/add_ctc_vcf.py
import io
import re
from telegram import InputFile, InlineKeyboardButton, InlineKeyboardMarkup

# =========================
# Helpers: normalisasi nomor
# =========================
def _fallback_clean(s: str) -> str:
    return re.sub(r"[^\d+]", "", (s or "").strip())

try:
    # jika punya utils, pakai
    from utils import clean_phone_number as _clean_phone, normalize_phone as _norm_phone
    def _normalize_phone(raw: str) -> str:
        return _norm_phone(_clean_phone(raw))
except Exception:
    # fallback sederhana
    def _normalize_phone(raw: str) -> str:
        s = _fallback_clean(raw)
        if not s:
            return ""
        return s if s.startswith("+") else "+" + s

# =========================
# Helpers: VCF
# =========================
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

def _dump(blocks):
    out = []
    for b in blocks:
        out.extend(b)
        if not b or b[-1].strip().upper() != "END:VCARD":
            out.append("END:VCARD")
        out.append("")
    return "\n".join(out).strip() + "\n"

def _extract_first_fn(text: str):
    for ln in (text or "").splitlines():
        if ln.upper().startswith("FN:"):
            return ln[3:].strip()
    return None

def _all_fns_from_blocks(blocks) -> list[str]:
    fns = []
    for b in blocks:
        for ln in b:
            if ln.upper().startswith("FN:"):
                fns.append(ln[3:].strip())
                break
    return fns

_NUM_PAT = re.compile(r"^(.*?)(?:\s*[-_ ]\s*)?(\d+)$")

def _analyze_sequence(fns: list[str]) -> tuple[str | None, int | None]:
    """
    Deteksi pola 'BASENAME <angka>' dari daftar FN.
    Return (basename, next_index). Ambil indeks terbesar + 1.
    Dipakai untuk opsi SELESAI (tanpa nama khusus).
    """
    best_base, best_max = None, None
    for name in fns:
        m = _NUM_PAT.match(name)
        if not m:
            continue
        base = m.group(1).strip()
        idx = int(m.group(2))
        if best_max is None or idx > best_max:
            best_base, best_max = base, idx
    return (best_base, (best_max + 1) if best_max is not None else None)

def _make_vcard(name: str, phone: str) -> list[str]:
    return [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{name}",
        f"TEL;TYPE=CELL:{phone}",
        "END:VCARD",
    ]

# =========================
# UI
# =========================
def _kb_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("📝 Nama Khusus", callback_data="addctc_name"),
            InlineKeyboardButton("✅ Selesai", callback_data="addctc_done"),
        ]]
    )

# =========================
# Handler
# =========================
class AddCtcVcfHandler:
    """
    Flow:
      1) start_mode: minta upload 1 VCF
      2) handle_document: terima VCF → minta daftar nomor (multi-baris)
      3) handle_text: terima nomor → tampilkan tombol (Nama Khusus / Selesai) + preview
      4) Klik Nama Khusus → bot minta *nama dasar* SEKALI (tanpa menampilkan nomor)
         - User mengetik, semua nomor dinamai: "NamaDasar 1..N" (selalu mulai dari 1)
      5) Klik Selesai (tanpa Nama Khusus) → nama mengikuti urutan lama (basename + lanjut index),
         jika tidak ada pola → pakai FN pertama; jika masih tidak ada → pakai nomornya.
      6) Kirim file (nama sama persis seperti input), lalu kirim info ringkas.
    """

    async def start_mode(self, query, context):
        context.user_data.clear()
        context.user_data.update({
            "waiting_for_add_vcf_file": True,
            "waiting_for_phone_to_add": False,
            "waiting_for_batch_name": False,  # mode input nama dasar sekali
            "add_vcf": {},          # {'fname','text','blocks','default_fn','seq_base','seq_next'}
            "add_queue": [],        # list[str] nomor baru
            "add_named": {},        # {phone: name} (jika Nama Khusus dipakai)
        })
        text = (
            "➕ *ADD CTC VCF*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📎 Upload *1 file .vcf* terlebih dahulu.\n"
            "Lalu kirim *daftar nomor* (multi-baris, 1 baris = 1 nomor)."
        )
        await query.edit_message_text(text, parse_mode="Markdown")

    # ===== Upload VCF =====
    async def handle_document(self, update, context):
        if not context.user_data.get("waiting_for_add_vcf_file"):
            return

        doc = update.message.document
        if not doc or not doc.file_name.lower().endswith(".vcf"):
            await update.message.reply_text("❌ Hanya menerima file .vcf")
            return

        tg = await context.bot.get_file(doc.file_id)
        data = await tg.download_as_bytearray()
        text = data.decode("utf-8", errors="ignore")

        blocks = _parse_blocks(text)
        fns = _all_fns_from_blocks(blocks)
        seq_base, seq_next = _analyze_sequence(fns)

        context.user_data["add_vcf"] = {
            "fname": doc.file_name,
            "text": text,
            "blocks": blocks,
            "default_fn": _extract_first_fn(text),  # fallback
            "seq_base": seq_base,                   # basis penomoran (jika ada)
            "seq_next": seq_next,                   # index awal berikutnya (jika ada)
        }
        context.user_data["waiting_for_add_vcf_file"] = False
        context.user_data["waiting_for_phone_to_add"] = True

        await update.message.reply_text(
            "📞 Kirim *nomor telepon* (multi-baris).",
            parse_mode="Markdown",
        )

    # ===== Input daftar nomor / nama dasar =====
    async def handle_text(self, update, context):
        # (1) Input daftar nomor
        if context.user_data.get("waiting_for_phone_to_add"):
            raw = update.message.text or ""
            nums = [_normalize_phone(x) for x in raw.splitlines() if _normalize_phone(x)]
            if not nums:
                await update.message.reply_text("❌ Tidak ada nomor valid. Kirim lagi.")
                return

            context.user_data["add_queue"] = nums
            context.user_data["waiting_for_phone_to_add"] = False

            # Preview default (tanpa Nama Khusus) sekadar gambaran
            v = context.user_data.get("add_vcf", {})
            seq_base, seq_next = v.get("seq_base"), v.get("seq_next")
            default_name = v.get("default_fn") or "Contact"

            preview_pairs, running = [], seq_next
            for ph in nums:
                if seq_base and running is not None:
                    nm = f"{seq_base} {running}"
                    running += 1
                else:
                    nm = default_name
                preview_pairs.append((nm, ph))

            if len(preview_pairs) > 10:
                preview_pairs = preview_pairs[:9] + [("…", nums[-1])]
            preview_lines = "\n".join([f"• {nm} → {ph}" for nm, ph in preview_pairs])

            text = (
                "🗒️ *Antrean dibuat*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• *Total nomor:* **{len(nums)}**\n"
                f"• *Preview (maks 10):*\n{preview_lines}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Pilih aksi:"
            )
            await update.message.reply_text(text, reply_markup=_kb_actions(), parse_mode="Markdown")
            return

        # (2) Input NAMA DASAR (sekali untuk semua nomor)
        if context.user_data.get("waiting_for_batch_name"):
            base = (update.message.text or "").strip()
            if not base:
                await update.message.reply_text("❌ Nama dasar kosong. Ketik lagi.")
                return

            nums = context.user_data.get("add_queue") or []
            # Buat mapping nama = "base 1..N" (SELALU mulai dari 1)
            context.user_data["add_named"] = {ph: f"{base} {i+1}" for i, ph in enumerate(nums)}
            context.user_data["waiting_for_batch_name"] = False

            # Finalize langsung
            await self._finalize(update.message, context)
            return

    # ===== Tombol (Nama Khusus / Selesai) =====
    async def handle_callback(self, query, context):
        data = query.data

        if data == "addctc_name":
            # Pastikan sudah ada daftar nomor
            nums = context.user_data.get("add_queue") or []
            if not nums:
                await query.edit_message_text(
                    "❌ Belum ada daftar nomor.\n"
                    "Kirim *nomor telepon* (multi-baris) dulu.",
                    parse_mode="Markdown",
                )
                return

            # Masuk mode input NAMA DASAR (sekali untuk semua nomor)
            context.user_data["waiting_for_batch_name"] = True
            context.user_data["waiting_for_phone_to_add"] = False  # pastikan tak bentrok
            context.user_data["waiting_for_custom_name"] = None     # tidak dipakai lagi

            await query.edit_message_text(
                "📝 Ketik *nama dasar kontak*.\n"
                "_Contoh: Admin, HHD, Kontak Baru_",
                parse_mode="Markdown",
            )
            return

        if data == "addctc_done":
            await self._finalize(query, context)
            return

    # ===== Finalize =====
    async def _finalize(self, target, context):
        vcf = context.user_data.get("add_vcf", {})
        queue = context.user_data.get("add_queue", [])
        named = context.user_data.get("add_named", {})  # jika Nama Khusus dipakai
        if not vcf or not queue:
            msg = getattr(target, "edit_message_text", None) or getattr(target, "reply_text", None)
            if msg:
                await msg("❌ Tidak ada data untuk diproses.")
            context.user_data.clear()
            return

        base_blocks = vcf.get("blocks", [])
        default_fn = vcf.get("default_fn")
        seq_base, seq_next = vcf.get("seq_base"), vcf.get("seq_next")

        new_blocks = []

        if named:
            # Sudah ada nama batch "Base 1..N"
            for ph in queue:
                new_blocks.append(_make_vcard(named[ph], ph))
        else:
            # Tanpa nama khusus: lanjut urutan lama / FN pertama / nomor
            running = seq_next
            for ph in queue:
                if seq_base and running is not None:
                    nm = f"{seq_base} {running}"
                    running += 1
                else:
                    nm = default_fn or ph
                new_blocks.append(_make_vcard(nm, ph))

        out_txt = _dump(base_blocks + new_blocks)

        # Nama file output SAMA seperti input
        out_name = vcf.get("fname", "contacts.vcf")

        bio = io.BytesIO(out_txt.encode("utf-8"))
        bio.name = out_name

        # 1) kirim file dulu (tanpa caption)
        msg_target = target.message if hasattr(target, "message") else target
        await msg_target.reply_document(InputFile(bio))

        # 2) lalu info ringkas
        info = (
            "✅ *ADD CTC VCF selesai*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 *File:* `{out_name}`\n"
            f"📊 *Ditambah:* **{len(queue)}** kontak\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Ketik */start* untuk kembali ke menu utama."
        )
        await msg_target.reply_text(info, parse_mode="Markdown")

        # bersih
        context.user_data.clear()
