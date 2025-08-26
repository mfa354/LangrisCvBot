# features/text_to_vcf.py
import logging
import contextlib
import asyncio
from io import BytesIO
from typing import Dict, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile

from config import get_instruction, SLEEP_BETWEEN_FILES
from utils import (
    # FORMAT
    create_vcf_content,
    # UTIL untuk INPUT
    extract_phone_numbers, clean_phone_number, normalize_phone,
    create_vcf_from_phones, clean_name_for_vcf
)

logger = logging.getLogger(__name__)

# Batas aman input teks (FORMAT)
MAX_TEXT_CHARS = 200_000
# Ringkasan preview
PREVIEW_CONTACT_LIMIT = 12


def _plural(n: int, satu: str, banyak: str) -> str:
    return f"{n} {satu if n == 1 else banyak}"


def _build_info_text(filename: str, contact_stats: Dict[str, int]) -> str:
    """
    Teks keterangan SERAGAM untuk MODE FORMAT & MODE INPUT.
    (ini meniru gaya yang kamu sebut 'format')
    """
    total_contacts = len(contact_stats)
    total_numbers = sum(contact_stats.values())

    items = list(contact_stats.items())
    preview = items[: max(PREVIEW_CONTACT_LIMIT, 0)]
    hidden = max(0, total_contacts - len(preview))

    lines = [
        f"✅ *Berhasil membuat:* `{filename}`",
        f"📁 {_plural(total_contacts,'kontak','kontak')} · 📞 {_plural(total_numbers,'nomor','nomor')}",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if items:
        title = "📊 *Ringkasan*"
        if total_contacts > PREVIEW_CONTACT_LIMIT:
            title += f" _(menampilkan ≤ {PREVIEW_CONTACT_LIMIT})_"
        lines.append(title)

        dot = "•"
        for name, cnt in preview:
            lines.append(f"{dot} *{name}* — {_plural(cnt,'nomor','nomor')}")
        if hidden:
            lines.append(f"_dan {hidden} entri lain tidak ditampilkan_")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━")

    lines.append("Ketik */start* untuk kembali ke menu.")
    return "\n".join(lines)


async def _send_vcf_then_info(update, filename: str, vcf_content: str, info_text: str):
    """Kirim FILE dulu (tanpa caption), lalu kirim keterangan terpisah."""
    data = BytesIO(vcf_content.encode('utf-8'))
    data.name = filename
    # 1) FILE tanpa caption
    await update.message.reply_document(
        document=InputFile(data, filename=filename)
    )
    # 2) INFO teks menyusul
    await update.message.reply_text(info_text, parse_mode='Markdown', disable_web_page_preview=True)


class TextToVCFHandler:
    """
    Handler:
    - FORMAT (lama): input teks → VCF (kirim file dulu, info menyusul)
    - INPUT (baru): step-by-step (Admin/Navy) — kirim file dulu, info menyusul
    """

    # =========================
    # FORMAT (lama)
    # =========================
    async def start_text_mode(self, update, context):
        try:
            context.user_data.clear()
            context.user_data.update({'waiting_for_string': True})

            cq = getattr(update, "callback_query", None)
            msg_text = get_instruction('text_instruction')
            if cq:
                await cq.edit_message_text(msg_text, parse_mode='Markdown')
            else:
                await update.message.reply_text(msg_text, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"[TextToVCF] start_text_mode error: {e}")
            with contextlib.suppress(Exception):
                await self._reply(update, "❌ Terjadi kesalahan saat membuka mode.")

    async def handle_text_input(self, update, context):
        """Router input teks untuk FORMAT & INPUT."""
        try:
            # ===== MODE INPUT — Admin numbers (multi-baris) =====
            if context.user_data.get('waiting_for_admin_phone'):
                raw = (update.message.text or "")
                lines: List[str] = extract_phone_numbers(raw)  # sudah buang baris kosong & jaga urutan
                phones: List[str] = []
                for ln in lines:
                    cleaned = clean_phone_number(ln)       # sisakan hanya + (maks 1 di depan) & digit
                    if not cleaned:
                        continue
                    normalized = normalize_phone(cleaned)  # tambah + hanya jika belum ada
                    phones.append(normalized)

                if not phones:
                    await update.message.reply_text(
                        "⚠️ Nomor tidak valid. Kirim *1 nomor per baris*.",
                        parse_mode='Markdown'
                    )
                    return

                context.user_data['admin_phones'] = phones
                context.user_data['waiting_for_admin_phone'] = False
                context.user_data['waiting_for_admin_name'] = True

                await update.message.reply_text(
                    "👤 *Nama Kontak*\nMasukkan *nama kontak* untuk nomor yang baru kamu kirim.",
                    parse_mode='Markdown'
                )
                return

            # ===== MODE INPUT — Admin name =====
            if context.user_data.get('waiting_for_admin_name'):
                name = (update.message.text or "").strip() or "Kontak"
                context.user_data['admin_name'] = clean_name_for_vcf(name)
                context.user_data['waiting_for_admin_name'] = False
                context.user_data['waiting_for_choice'] = True

                kb = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("➕ Tambah kontak", callback_data='input_add_navy'),
                        InlineKeyboardButton("▶️ Next", callback_data='input_admin_only')
                    ]
                ])
                await update.message.reply_text(
                    "Pilih tindakan:\n"
                    "• *Tambah kontak* → kembali ke input *nomor* untuk kontak berikutnya\n"
                    "• *Next* → lanjut *minta nama file*",
                    reply_markup=kb, parse_mode='Markdown'
                )
                return

            # ===== MODE INPUT — Navy numbers (multi-baris) =====
            if context.user_data.get('waiting_for_navy_phone'):
                raw = (update.message.text or "")
                lines: List[str] = extract_phone_numbers(raw)
                phones: List[str] = []
                for ln in lines:
                    cleaned = clean_phone_number(ln)
                    if not cleaned:
                        continue
                    normalized = normalize_phone(cleaned)
                    phones.append(normalized)

                if not phones:
                    await update.message.reply_text(
                        "⚠️ Nomor tidak valid. Kirim *1 nomor per baris*.",
                        parse_mode='Markdown'
                    )
                    return

                context.user_data['navy_phones'] = phones
                context.user_data['waiting_for_navy_phone'] = False
                context.user_data['waiting_for_navy_name'] = True

                await update.message.reply_text(
                    "👤 *Nama Kontak*\nMasukkan *nama kontak* untuk nomor yang baru kamu kirim.",
                    parse_mode='Markdown'
                )
                return

            # ===== MODE INPUT — Navy name =====
            if context.user_data.get('waiting_for_navy_name'):
                name = (update.message.text or "").strip() or "Kontak"
                context.user_data['navy_name'] = clean_name_for_vcf(name)
                context.user_data['waiting_for_navy_name'] = False
                context.user_data['waiting_for_filename'] = True

                await update.message.reply_text(
                    "📝 *Nama File Output*\nMasukkan *nama file VCF* (boleh tanpa *.vcf*).",
                    parse_mode='Markdown'
                )
                return

            # ===== MODE INPUT — filename =====
            if context.user_data.get('waiting_for_filename'):
                filename = (update.message.text or "").strip()
                if not filename:
                    await update.message.reply_text("⚠️ Nama file tidak boleh kosong.")
                    return
                if not filename.lower().endswith('.vcf'):
                    filename += '.vcf'
                context.user_data['output_filename'] = filename

                await self._finalize_input_mode(update, context)
                return

            # ===== MODE FORMAT (lama) =====
            if context.user_data.get('waiting_for_string'):
                text_input = (update.message.text or "").strip()

                if len(text_input) > MAX_TEXT_CHARS:
                    await self._reply(
                        update,
                        f"❌ Teks terlalu besar (>{MAX_TEXT_CHARS:,} karakter). "
                        "Potong input atau kirim bertahap."
                    )
                    return

                vcf_content, filename, contact_stats = create_vcf_content(text_input)
                if not vcf_content or not filename or not contact_stats:
                    await self._reply(update, self._format_error_help())
                    context.user_data.clear()
                    return

                info_text = _build_info_text(filename, contact_stats)

                # === Kirim FILE dulu, lalu INFO (tanpa caption) ===
                await _send_vcf_then_info(update, filename, vcf_content, info_text)
                await asyncio.sleep(SLEEP_BETWEEN_FILES)
                context.user_data.clear()
                return

        except Exception as e:
            logger.error(f"[TextToVCF] handle_text_input error: {e}")
            with contextlib.suppress(Exception):
                await self._reply(
                    update,
                    "❌ Gagal memproses input. Coba lagi."
                )
            context.user_data.clear()

    # =========================
    # INPUT — entry & choice
    # =========================
    async def start_input_mode(self, update, context):
        """Mode INPUT: minta nomor Admin terlebih dahulu (wording diperbarui)."""
        try:
            context.user_data.clear()
            context.user_data.update({
                'input_mode': True,
                'waiting_for_admin_phone': True
            })

            cq = getattr(update, "callback_query", None)
            msg = (
                "⌨️ *MODE INPUT — CV ADMIN/VCF*\n\n"
                "🗒️ *Kirim Nomor Admin/Navy dulu.*\n"
                "• 1 baris = 1 nomor\n"
                "• Simbol/spasi dibersihkan otomatis\n"
                "• Perhatikan *kode negara* agar terbaca\n"
            )
            if cq:
                await cq.edit_message_text(msg, parse_mode='Markdown')
            else:
                await update.message.reply_text(msg, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"[TextToVCF] start_input_mode error: {e}")
            with contextlib.suppress(Exception):
                await self._reply(update, "❌ Gagal membuka mode INPUT.")

    async def handle_input_choice(self, query, context):
        """Tangani tombol 'Tambah kontak' atau 'Selesai' (label diperbarui)."""
        try:
            if not context.user_data.get('input_mode'):
                await query.edit_message_text("❌ Mode INPUT tidak aktif. Gunakan /start.", parse_mode='Markdown')
                return

            choice = query.data
            context.user_data['waiting_for_choice'] = False

            if choice == 'input_add_navy':
                context.user_data['want_navy'] = True
                context.user_data['waiting_for_navy_phone'] = True
                await query.edit_message_text(
                    "⌨️ *MODE INPUT — CV ADMIN/VCF*\n\n"
                    "🗒️ *Kirim Nomor Admin/Navy berikutnya.*\n"
                    "• 1 baris = 1 nomor\n"
                    "• Simbol/spasi dibersihkan otomatis\n"
                    "• Perhatikan *kode negara* agar terbaca\n",
                    parse_mode='Markdown'
                )
            else:
                context.user_data['want_navy'] = False
                context.user_data['waiting_for_filename'] = True
                await query.edit_message_text(
                    "📝 *Nama File Output*\nMasukkan *nama file VCF* (boleh tanpa *.vcf*).",
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.error(f"[TextToVCF] handle_input_choice error: {e}")
            with contextlib.suppress(Exception):
                await query.edit_message_text("❌ Terjadi kesalahan. /start untuk ulang.", parse_mode='Markdown')
            context.user_data.clear()

    # =========================
    # INPUT — finalize
    # =========================
    async def _finalize_input_mode(self, update, context):
        """
        Bangun VCF dari data yang dikumpulkan & kirim.
        - Admin wajib ada; Navy opsional.
        - Multi nomor: penomoran 1..N per kontak (force_numbering=True).
        - FILE tanpa caption, lalu INFO dipisah (dengan format seragam).
        """
        try:
            admin_phones: List[str] = context.user_data.get('admin_phones', []) or []
            admin_name: str = context.user_data.get('admin_name', 'Admin')
            want_navy: bool = context.user_data.get('want_navy', False)
            navy_phones: List[str] = context.user_data.get('navy_phones', []) or []
            navy_name: str = context.user_data.get('navy_name', 'Navy')
            filename: str = context.user_data.get('output_filename')

            if not admin_phones or not admin_name or not filename:
                await update.message.reply_text("❌ Data belum lengkap. /start untuk mulai lagi.")
                context.user_data.clear()
                return

            # Bangun konten VCF
            parts = []
            parts.append(create_vcf_from_phones(admin_phones, admin_name, force_numbering=True))
            if want_navy and navy_phones:
                parts.append(create_vcf_from_phones(navy_phones, navy_name, force_numbering=True))
            vcf_content = "".join(parts)

            # Info (seragam seperti MODE FORMAT)
            stats: Dict[str, int] = {admin_name: len(admin_phones)}
            if want_navy and navy_phones:
                stats[navy_name] = len(navy_phones)
            info_text = _build_info_text(filename, stats)

            # === Kirim FILE dulu, lalu INFO ===
            await _send_vcf_then_info(update, filename, vcf_content, info_text)
            await asyncio.sleep(SLEEP_BETWEEN_FILES)
            context.user_data.clear()

        except Exception as e:
            logger.error(f"[TextToVCF] finalize_input_mode error: {e}")
            with contextlib.suppress(Exception):
                await update.message.reply_text("❌ Gagal membuat VCF. /start untuk mulai lagi.")
            context.user_data.clear()

    # ===== helpers =====
    async def _reply(self, update, text: str):
        if getattr(update, "message", None):
            await update.message.reply_text(text, parse_mode='Markdown')
        elif getattr(update, "callback_query", None):
            await update.callback_query.edit_message_text(text, parse_mode='Markdown')

    def _format_error_help(self) -> str:
        return (
            "❌ *Format salah.* Contoh:\n"
            "```\n"
            "nama_file_vcf\n"
            "\n"
            "Nama Kontak 1\n"
            "628xxx\n"
            "628yyy\n"
            "\n"
            "Nama Kontak 2\n"
            "628zzz\n"
            "```\n"
            "• Baris-1: nama file (tanpa .vcf boleh)\n"
            "• Baris-2: *KOSONG* (pemisah)\n"
            "• Tiap kontak dipisah baris kosong\n"
            "• Dalam satu kontak: daftar nomor di baris berikutnya\n"
        )
