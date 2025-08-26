import re
import io
import asyncio
import time
from typing import Optional

# =========================
# Helpers & Normalizers
# =========================
def clean_name_for_vcf(name: str) -> str:
    """
    Bersihkan nama agar kompatibel VCF tapi tetap mempertahankan emoji/simbol.
    Hanya hilangkan ; dan newline, serta rapikan spasi.
    """
    cleaned = re.sub(r'[;\n\r]', ' ', str(name))
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def extract_phone_numbers(text: str) -> list:
    """
    Ambil nomor telepon dari teks.
    Setiap baris dianggap satu entitas nomor (tidak pakai regex substring).
    Spasi kosong diabaikan.
    """
    if not isinstance(text, str):
        return []

    lines = text.splitlines()
    phones = [ln.strip() for ln in lines if ln.strip()]

    # hapus duplikat tapi pertahankan urutan
    return list(dict.fromkeys(phones))

def normalize_phone(phone: str) -> str:
    """Tambah prefix + kalau belum ada. Tidak asumsi kode negara."""
    phone = str(phone).strip()
    return phone if phone.startswith('+') else '+' + phone

def normalize_phone_for_txt_output(phone: str) -> str:
    """Untuk output TXT (fitur VCF->TXT): tambah + kalau belum ada."""
    phone = str(phone).strip()
    return phone if phone.startswith('+') else '+' + phone

def normalize_phone_list_format(phone_list: list) -> list:
    """Normalisasi list nomor: pastikan semua diawali '+' (dipakai TXT->VCF)."""
    if not phone_list:
        return []
    out = []
    for p in phone_list:
        p = str(p).strip()
        if not p.startswith('+'):
            p = '+' + p
        out.append(p)
    return out

# =========================
# VCF parsing/creation
# =========================
def parse_vcf_content(vcf_content: str) -> list:
    """Parse isi VCF -> [{'name': ..., 'phone': ...}, ...]"""
    if not isinstance(vcf_content, str) or not vcf_content:
        return []

    contacts = []
    vcards = re.findall(r'BEGIN:VCARD.*?END:VCARD', vcf_content, re.DOTALL)

    for vcard in vcards:
        name_match = re.search(r'FN:(.+)', vcard)
        tel_match = re.search(r'TEL:(.+)', vcard)
        if name_match and tel_match:
            name = name_match.group(1).strip()
            phone = tel_match.group(1).strip()
            contacts.append({'name': name, 'phone': phone})

    return contacts

def clean_phone_number(phone: str) -> str:
    """Bersihkan nomor: sisakan digit dan +; rapikan tanda + ganda."""
    if not phone:
        return phone
    cleaned = re.sub(r'[^\d+]', '', str(phone).strip())
    if cleaned.startswith('+'):
        cleaned = '+' + re.sub(r'\+', '', cleaned[1:])
    else:
        cleaned = re.sub(r'\+', '', cleaned)
    return cleaned

def create_vcf_content(text_input: str):
    """
    Konversi format string (mode /string) ke VCF.
    Return: (vcf_content, filename, contact_stats) atau (None, None, None) jika invalid.
    """
    if not isinstance(text_input, str):
        return None, None, None

    lines = text_input.split('\n')

    # buang trailing empty lines
    while lines and not lines[-1].strip():
        lines.pop()

    if len(lines) < 4:  # minimal: filename, blank line, name, phone
        return None, None, None

    filename = lines[0].strip()
    if not filename.endswith('.vcf'):
        filename += '.vcf'

    # baris kedua harus kosong
    if lines[1].strip():
        return None, None, None

    # mulai blok kontak dari baris ke-3
    contact_lines = lines[2:]

    # split per blok (dipisah baris kosong)
    blocks = []
    cur = []
    for line in contact_lines:
        if line.strip():
            cur.append(line.strip())
        else:
            if cur:
                blocks.append(cur)
                cur = []
    if cur:
        blocks.append(cur)

    if not blocks:
        return None, None, None

    vcf_content = ""
    stats = {}

    for block in blocks:
        if len(block) < 2:
            continue
        name_base = clean_name_for_vcf(block[0])
        phones = block[1:]
        stats[name_base] = len(phones)

        for i, phone in enumerate(phones, 1):
            clean_phone = clean_phone_number(phone)
            if clean_phone and not clean_phone.startswith('+'):
                clean_phone = '+' + clean_phone
            name = f"{name_base} {i}" if len(phones) > 1 else name_base
            vcf_content += (
                "BEGIN:VCARD\nVERSION:3.0\n"
                f"FN:{name}\nTEL:{clean_phone}\nEND:VCARD\n"
            )

    return vcf_content, filename, stats

def create_vcf_from_phones(
    phone_numbers: list,
    contact_name: str,
    start_index: Optional[int] = None,
    force_numbering: bool = False
) -> str:
    """
    Buat VCF dari list nomor.
    - Default (start_index=None, force_numbering=False): perilaku lama
      -> penomoran hanya muncul jika 1 nama punya >1 nomor.
    - Global numbering (start_index=angka):
      -> SELALU beri akhiran nomor berurutan berbasis start_index (lintas file/batch).
    - force_numbering=True (tanpa start_index):
      -> selalu beri akhiran 1..N untuk list ini saja (local numbering).
    """
    contact_name = clean_name_for_vcf(contact_name)
    vcf = ""

    # Normalisasi nomor dulu (pastikan ada '+')
    normalized = normalize_phone_list_format(phone_numbers)

    # Mode global numbering: selalu pakai akhiran index absolut
    if start_index is not None:
        idx = int(start_index)
        for phone in normalized:
            name = f"{contact_name} {idx}"
            vcf += (
                "BEGIN:VCARD\nVERSION:3.0\n"
                f"FN:{name}\nTEL:{phone}\nEND:VCARD\n"
            )
            idx += 1
        return vcf

    # Mode lama / local numbering
    for i, phone in enumerate(normalized, 1):
        if force_numbering or len(normalized) > 1:
            name = f"{contact_name} {i}"
        else:
            name = contact_name
        vcf += (
            "BEGIN:VCARD\nVERSION:3.0\n"
            f"FN:{name}\nTEL:{phone}\nEND:VCARD\n"
        )
    return vcf

def create_vcf_from_contacts(contacts: list) -> str:
    """Buat VCF dari list dict contacts."""
    vcf = ""
    for c in contacts or []:
        vcf += (
            "BEGIN:VCARD\nVERSION:3.0\n"
            f"FN:{c['name']}\nTEL:{c['phone']}\nEND:VCARD\n"
        )
    return vcf

def create_txt_from_vcf(contacts: list) -> str:
    """
    VCF -> TXT (satu nomor per baris).
    Tetap tambahkan '+' jika belum ada.
    """
    if not contacts:
        return ""
    out = []
    for c in contacts:
        phone = normalize_phone_for_txt_output(c['phone'])
        if phone not in out:
            out.append(phone)
    return '\n'.join(out)

def generate_custom_filenames(base_name: str, total_files: int) -> list:
    """Buat daftar nama file berurutan dari base_name yang diakhiri angka."""
    m = re.search(r'(.+?)(\d+)$', str(base_name))
    if not m:
        return []
    base_part, start_num = m.group(1), int(m.group(2))
    return [f"{base_part}{start_num + i}.vcf" for i in range(total_files or 0)]

def split_phones_into_batches(phones: list, contacts_per_file: int, total_files: int) -> list:
    """
    Bagi list nomor menjadi beberapa batch.
    (Dipakai V2 TXT->VCF)
    """
    batches = []
    if not phones or contacts_per_file <= 0 or total_files <= 0:
        return batches

    phones_per_batch = len(phones) // total_files
    remainder = len(phones) % total_files

    start = 0
    for i in range(total_files):
        batch_size = min(contacts_per_file, phones_per_batch + (1 if i < remainder else 0))
        end = min(start + batch_size, len(phones))
        batches.append(phones[start:end])
        start = end
        if start >= len(phones):
            break
    return [b for b in batches if b]

# =========================
# MERGE helpers (TXT/VCF)
# =========================
def merge_txt_files(txt_files_data: list) -> list:
    """
    MERGE TXT: HANYA menggabungkan baris non-kosong dari semua file
    sesuai urutan upload. Tidak ada normalisasi, tidak ada dedup,
    tidak mengubah karakter (emoji/simbol tetap).
    Struktur item: {'filename': str, 'lines': List[str]}
    Return: List[str] (baris gabungan)
    """
    merged = []
    for item in txt_files_data or []:
        for line in item.get('lines', []):
            if str(line).strip() == "":
                continue
            # jangan ubah isi baris
            merged.append(line)
    return merged

def merge_vcf_files(vcf_files_data: list) -> list:
    """
    MERGE VCF: gabungkan semua kontak dan HAPUS duplikat (name+phone).
    Struktur item: {'filename': str, 'contacts': List[{'name':..., 'phone':...}]}
    Return: List[contacts]
    """
    all_contacts = []
    seen = set()
    for item in vcf_files_data or []:
        for c in item.get('contacts', []):
            cid = f"{c['name']}|{c['phone']}"
            if cid not in seen:
                seen.add(cid)
                all_contacts.append(c)
    return all_contacts

# =========================
# I/O helpers (Telegram)
# =========================
async def send_vcf_file(update, filename: str, vcf_content: str, stats_msg: str = None):
    """Kirim file VCF dengan caption opsional."""
    data = io.BytesIO(vcf_content.encode('utf-8'))
    data.name = filename
    await update.message.reply_document(
        document=data,
        filename=filename,
        caption=stats_msg,
        parse_mode='Markdown' if stats_msg else None
    )

async def send_txt_file(update, filename: str, txt_content: str, stats_msg: str = None):
    """Kirim file TXT dengan caption opsional."""
    data = io.BytesIO(txt_content.encode('utf-8'))
    data.name = filename
    await update.message.reply_document(
        document=data,
        filename=filename,
        caption=stats_msg,
        parse_mode='Markdown' if stats_msg else None
    )

def read_file_content(file_content: bytearray) -> str:
    """
    Baca konten file dengan beberapa fallback encoding.
    Emoji/simbol aman karena prioritas UTF-8.
    """
    if file_content is None:
        return None
    for enc in ('utf-8', 'latin-1', 'cp1252', 'iso-8859-1'):
        try:
            return file_content.decode(enc)
        except UnicodeDecodeError:
            continue
    return None
