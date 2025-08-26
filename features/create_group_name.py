# features/create_group_name.py
import logging
import contextlib
import re

logger = logging.getLogger(__name__)

class CreateGroupNameHandler:
    """
    CREATE GROUP NAME
    - Step 1: minta nama dasar (wajib diakhiri angka), contoh: 'Team🔥1', 'group-99'
    - Step 2: minta jumlah group (integer > 0)
    - Output: kirim teks yang bisa disalin langsung, bukan file
    """

    async def start_mode(self, query, context):
        try:
            context.user_data.clear()
            context.user_data.update({'waiting_for_group_basename': True})
            text = (
                "👥 *CREATE GROUP NAME*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Ketik *nama dasar* (wajib diakhiri angka).\n"
                "Contoh: `Squad🔥1`, `grup-10`, `Batch_001`\n"
            )
            await query.edit_message_text(text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"[CreateGroup] start_mode error: {e}")

    async def handle_text(self, update, context):
        try:
            # Step 1: base name
            if context.user_data.get('waiting_for_group_basename'):
                base = (update.message.text or "").strip()
                if not re.search(r'\d+$', base):
                    await update.message.reply_text(
                        "❌ Nama dasar *wajib diakhiri angka*.\ncontoh: `Team🔥1` atau `group-99`",
                        parse_mode='Markdown'
                    )
                    return
                context.user_data['group_base'] = base
                context.user_data['waiting_for_group_basename'] = False
                context.user_data['waiting_for_group_count'] = True
                await update.message.reply_text(
                    "🧮 *Jumlah group?* Ketik angka > 0", parse_mode='Markdown'
                )
                return

            # Step 2: count
            if context.user_data.get('waiting_for_group_count'):
                raw = (update.message.text or "").strip()
                try:
                    n = int(raw)
                    if n <= 0:
                        raise ValueError()
                except Exception:
                    await update.message.reply_text("❌ Masukkan angka valid (> 0).")
                    return

                base = context.user_data.get('group_base')
                m = re.search(r'(.+?)(\d+)$', base)
                prefix, start = m.group(1), int(m.group(2))

                names = [f"{prefix}{start + i}" for i in range(n)]
                content = "\n".join(names)

                await update.message.reply_text(
                    "✅ *Selesai!* {n} nama dibuat.\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"```\n{content}\n```",
                    parse_mode='Markdown'
                )
                context.user_data.clear()
                return

        except Exception as e:
            logger.error(f"[CreateGroup] handle_text error: {e}")
            with contextlib.suppress(Exception):
                await update.message.reply_text("❌ Terjadi kesalahan. /start untuk ulang.")
            context.user_data.clear()
