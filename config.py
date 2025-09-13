import os
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Load env
load_dotenv()

# =========================
# Bot Configuration
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

# =========================
# OWNER & ADMIN SETTINGS
# =========================
# Bisa lebih dari 1 owner (misalnya owner utama + cadangan)
OWNER_IDS = {
    7096405831,   # Owner utama
    1234567890,   # Owner cadangan (ganti dengan ID numeric)
}

ADMIN_IDS = set(OWNER_IDS)  # Semua owner otomatis admin

def is_owner(uid: int) -> bool:
    """Cek apakah user adalah owner."""
    try:
        return int(uid) in OWNER_IDS
    except Exception:
        return False

# =========================
# ACCESS GATE SETTINGS
# =========================
# Pastikan bot adalah ADMIN di channel & group ini
REQUIRED_CHANNEL = "@langrisinfo"
REQUIRED_GROUP   = "@langrismarket"

# Trial untuk user baru (dalam menit)
TRIAL_MINUTES = 30
TIMEZONE      = "Asia/Jakarta"

# =========================
# MENUS
# =========================
MENUS = {
    # ========= MAIN (Page 1) =========
    "main": {
        "text": "🤖 *WELCOME TO LANGRIS CV BOT V1*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Pilih menu di bawah ini:",
        "buttons": [
            [
                InlineKeyboardButton("📝 CV ADMIN/NAVY", callback_data="text_to_vcf"),
                InlineKeyboardButton("📁 CV TXT TO VCF", callback_data="cv_txt_to_vcf"),
            ],
            [
                InlineKeyboardButton("🔄 CV VCF TO TXT", callback_data="cv_vcf_to_txt"),
                InlineKeyboardButton("🔗 MERGE TXT/VCF", callback_data="merge_files"),
            ],
            [
                InlineKeyboardButton("🔢 COUNT VCF/TXT", callback_data="count_files"),
                InlineKeyboardButton("👥 GROUP NAME", callback_data="create_group_name"),
            ],
            [
                InlineKeyboardButton("⬅️", callback_data="nav_p1_left"),
                InlineKeyboardButton("🏠", callback_data="nav_home"),
                InlineKeyboardButton("➡️", callback_data="nav_p1_right"),
            ],
        ],
    },

    # ========= MAIN (Page 2) =========
    "main_page2": {
        "text": "🤖 *WELCOME TO LANGRIS CV BOT*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Pilih menu di bawah ini:",
        "buttons": [
            [
                InlineKeyboardButton("➕ ADD CTC VCF", callback_data="add_ctc_vcf"),
                InlineKeyboardButton("🗑️ REMOVE CTC VCF", callback_data="remove_ctc_vcf"),
            ],
            [
                InlineKeyboardButton("✏️ EDIT CTC NAME", callback_data="edit_ctc_name"),
                InlineKeyboardButton("📄 GET NAME FILE", callback_data="get_name_file"),
            ],
            [
                InlineKeyboardButton("✂️ SPLIT TXT/VCF", callback_data="split_files"),
                InlineKeyboardButton("📜 TXT/VCF TO TEXT", callback_data="txt_vcf_to_text"),
            ],
            [
                InlineKeyboardButton("⬅️", callback_data="nav_p2_left"),
                InlineKeyboardButton("🏠", callback_data="nav_home"),
                InlineKeyboardButton("➡️", callback_data="nav_p2_right"),
            ],
        ],
    },

    # ========= Submenu CV ADMIN/NAVY =========
    "text_submenu": {
        "text": "📝 *CV ADMIN/NAVY — Pilih Sub Fitur*\n\n"
                "• *FORMAT* → mekanisme input teks (sekali input langsung jadi)\n"
                "• *INPUT* → input satu-satu.",
        "buttons": [
            [
                InlineKeyboardButton("📄 FORMAT", callback_data="text_format"),
                InlineKeyboardButton("⌨️ INPUT", callback_data="text_input"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")],
        ],
    },

    # ========= Submenu TXT → VCF =========
    "cv_submenu": {
        "text": (
            "📁 *CV TXT TO VCF — Pilih Mode:*\n\n"
            "🔧 *DIRECT* → per file langsung jadi VCF\n"
            "🚀 *BATCH*  → pecah menjadi beberapa VCF"
        ),
        "buttons": [
            [
                InlineKeyboardButton("🔧 DIRECT", callback_data="cv_v1"),
                InlineKeyboardButton("🚀 BATCH", callback_data="cv_v2"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")],
        ],
    },

    # ========= Submenu MERGE =========
    "merge_submenu": {
        "text": "🔗 *MERGE TXT/VCF - Pilih Jenis File:*\n\n"
                "📄 *TXT* — Gabung beberapa file TXT menjadi satu\n"
                "📋 *VCF* — Gabung beberapa file VCF menjadi satu",
        "buttons": [
            [
                InlineKeyboardButton("📄 TXT", callback_data="merge_txt"),
                InlineKeyboardButton("📋 VCF", callback_data="merge_vcf"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")],
        ],
    },

    # ========= Pilihan output (TXT→VCF Direct) =========
    "output_mode_selection": {
        "text": "✅ *Upload selesai!*\n\n"
                "📋 *Pilih mode output:*\n\n"
                "🔹 **Default** — Nama file VCF sama dengan TXT\n"
                "🔹 **Custom** — Nama file VCF sesuai input Anda",
        "buttons": [
            [
                InlineKeyboardButton("🔹 Default", callback_data="output_default"),
                InlineKeyboardButton("🎨 Custom", callback_data="output_custom"),
            ]
        ],
    },

    # ========= Konfirmasi BATCH =========
    "v2_confirm": {
        "text": "🚀 *Mode BATCH - Konfirmasi*\n\n"
                "📋 *Detail:*\n{details}\n\n"
                "*Lanjutkan proses?*",
        "buttons": [
            [
                InlineKeyboardButton("✅ Lanjutkan", callback_data="v2_proceed"),
                InlineKeyboardButton("❌ Batal", callback_data="back_to_main"),
            ]
        ],
    },

    # ========= Pilih output VCF→TXT =========
    "vcf_to_txt_selection": {
        "text": "🔄 *VCF TO TXT - Pilih Output:*\n\n"
                "📋 *Detail:*\n{details}\n\n"
                "*Pilih mode konversi:*",
        "buttons": [
            [
                InlineKeyboardButton("📄 Selesai", callback_data="vcf_separate"),
                InlineKeyboardButton("🔗 Gabung", callback_data="vcf_merge"),
            ]
        ],
    },
}

# =========================
# Instruksi teks
# =========================
INSTRUCTIONS = {
    "cv_instruction": (
        "📤 *Upload file TXT Anda*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• Upload satu atau beberapa file .txt\n"
    ),
    "v2_instruction": (
        "🚀 *Mode BATCH — Upload File TXT*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📂 Upload 1–10 file TXT\n"
        "• Jika lebih dari 1 file → otomatis digabung\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    ),
    "text_instruction": (
        "📝 *Format input:*\n"
        "```\n"
        "nama_file_vcf\n"
        "\n"
        "nama kontak\n"
        "nomer telepon\n"
        "\n"
        "nama kontak\n"
        "nomer telepon\n"
        "```\n\n"
        "📌 *Contoh format Admin/Navy:*"
        "```\n"
        "Admin Navy\n"
        "\n"
        "Admin\n"
        "628123123123\n"
        "852123123123\n"
        "\n"
        "Navy\n"
        "123123123123\n"
        "441231231232\n"
        "712312312313\n"
        "```\n\n"
        "⚠️ Baris-1 = nama file, Baris-2 *KOSONG* (pemisah)."
    ),
}

# =========================
# Menu helper
# =========================
async def show_menu(message_target, menu_key, edit=False, **kwargs):
    menu = MENUS.get(menu_key)
    if not menu:
        return

    text = menu["text"].format(**kwargs) if kwargs else menu["text"]
    reply_markup = InlineKeyboardMarkup(menu["buttons"]) if "buttons" in menu else None

    try:
        if edit:
            if hasattr(message_target, "edit_message_text"):
                await message_target.edit_message_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )
            elif hasattr(message_target, "edit_text"):
                await message_target.edit_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )
            else:
                await message_target.reply_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )
        else:
            await message_target.reply_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            )
    except Exception as e:
        print(f"Error showing menu: {e}")

def get_instruction(key):
    return INSTRUCTIONS.get(key, "Instruksi tidak ditemukan.")

# =========================
# Settings fitur lain
# =========================
MAX_FILES_V2 = 10
UPLOAD_TIMEOUT = 3.0
SLEEP_BETWEEN_FILES = 0.3
