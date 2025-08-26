import asyncio
import time
import logging
import contextlib
from config import get_instruction, UPLOAD_TIMEOUT, MAX_FILES_V2, SLEEP_BETWEEN_FILES
from utils import (
    extract_phone_numbers, read_file_content, normalize_phone_list_format,
    create_vcf_from_phones, send_vcf_file, generate_custom_filenames,
    split_phones_into_batches
)

logger = logging.getLogger(__name__)

class TxtToVCFHandler:
    """Handler untuk konversi TXT → VCF (Mode V1 & V2) — UX/teks."""

    def __init__(self):
        self.file_locks = {}  # cegah pemrosesan ganda per file_id

    # =========================
    # Callback dari tombol UI
    # =========================
    async def handle_callback(self, query, context):
        try:
            data = query.data
            if data == 'cv_v1':
                await self.start_v1_mode(query, context)
            elif data == 'cv_v2':
                await self.start_v2_mode(query, context)

            elif data == 'output_default':
                await self.setup_default_output(query, context)
            elif data == 'output_custom':
                await self.setup_custom_output(query, context)

            # === V2: pilihan MODE setelah upload selesai ===
            elif data in ('v2_format', 'v2_input'):
                # simpan target mode, lalu siapkan merged_phones (single/batch)
                context.user_data['v2_target_mode'] = 'format' if data == 'v2_format' else 'input'
                txt_files = context.user_data.get('txt_files_data', [])
                if len(txt_files) <= 1:
                    await self._v2_prepare_single(query, context)
                else:
                    await self._v2_prepare_batch(query, context)

            # (legacy) tetap dukung jika masih ada tombol lama
            elif data == 'v2_proceed':
                context.user_data['v2_target_mode'] = 'format'  # default ke FORMAT
                txt_files = context.user_data.get('txt_files_data', [])
                if len(txt_files) <= 1:
                    await self._v2_prepare_single(query, context)
                else:
                    await self._v2_prepare_batch(query, context)

        except Exception as e:
            logger.error(f"Error in handle_callback: {e}")
            with contextlib.suppress(Exception):
                await query.edit_message_text("❌ Terjadi kesalahan. Silakan /start ulang.")

    # =========================
    # Init Mode
    # =========================
    async def start_v1_mode(self, query, context):
        try:
            context.user_data.clear()
            context.user_data.update({
                'cv_mode': 'v1',
                'waiting_for_txt_files': True,
                'txt_files_data': [],
                'user_id': query.from_user.id,
                'chat_id': query.message.chat.id,
            })
            await query.edit_message_text(get_instruction('cv_instruction'), parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in start_v1_mode: {e}")

    async def start_v2_mode(self, query, context):
        try:
            context.user_data.clear()
            context.user_data.update({
                'cv_mode': 'v2',
                'waiting_for_txt_files': True,
                'txt_files_data': [],
                'user_id': query.from_user.id,
                'chat_id': query.message.chat.id,
            })
            await query.edit_message_text(get_instruction('v2_instruction'), parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in start_v2_mode: {e}")

    # =========================
    # Upload Dokumen
    # =========================
    def _cancel_final_preview_task(self, context):
        task = context.user_data.get('final_preview_task')
        if task and not task.done():
            task.cancel()
        context.user_data['final_preview_task'] = None

    def _schedule_final_preview(self, update, context):
        """Jadwalkan preview final setelah idle UPLOAD_TIMEOUT detik."""
        self._cancel_final_preview_task(context)

        async def waiter():
            try:
                await asyncio.sleep(UPLOAD_TIMEOUT)
                last_ts = context.user_data.get('last_file_at', 0)
                if time.time() - last_ts >= UPLOAD_TIMEOUT - 0.05:
                    await self.show_files_preview(update, context, final=True)
            except asyncio.CancelledError:
                pass

        context.user_data['final_preview_task'] = asyncio.create_task(waiter())

    async def handle_document(self, update, context):
        """Tangani upload TXT (robust untuk batch)."""
        user_id = update.message.from_user.id

        if not context.user_data.get('waiting_for_txt_files', False):
            logger.info(f"Document from {user_id} ignored (not waiting).")
            return

        document = update.message.document

        # Validasi ekstensi
        if not document.file_name or not document.file_name.lower().endswith('.txt'):
            await update.message.reply_text("❌ Silakan upload file berformat .txt")
            return

        # Limit jumlah file khusus V2
        cv_mode = context.user_data.get('cv_mode', 'v1')
        current_files = len(context.user_data.get('txt_files_data', []))
        if cv_mode == 'v2' and current_files >= MAX_FILES_V2:
            await update.message.reply_text(f"❌ Mode V2 maksimal {MAX_FILES_V2} file!")
            return

        # Lock per file
        file_key = f"{user_id}_{document.file_id}"
        if file_key in self.file_locks:
            logger.warning(f"Skip duplicate processing: {document.file_name}")
            return
        self.file_locks[file_key] = True

        try:
            # Ukuran file
            if document.file_size and document.file_size > 20 * 1024 * 1024:
                logger.warning(f"Too large: {document.file_name}")
                return

            # Unduh
            try:
                tg_file = await asyncio.wait_for(
                    context.bot.get_file(document.file_id), timeout=15.0
                )
                file_content = await asyncio.wait_for(
                    tg_file.download_as_bytearray(), timeout=30.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout download: {document.file_name}")
                return
            except Exception as e:
                logger.error(f"Download error {document.file_name}: {e}")
                return

            # Baca isi
            try:
                text_content = read_file_content(file_content)
                if not text_content or len(text_content.strip()) == 0:
                    logger.warning(f"Empty content: {document.file_name}")
                    return
                if len(text_content) > 5 * 1024 * 1024:
                    logger.warning(f"Content too large: {document.file_name}")
                    return
            except Exception as e:
                logger.error(f"Read error {document.file_name}: {e}")
                return

            # Ekstrak nomor (setiap baris = 1 nomor)
            try:
                phone_numbers = extract_phone_numbers(text_content)
                if not phone_numbers:
                    logger.warning(f"No phones: {document.file_name}")
                    return
                if len(phone_numbers) > 50000:
                    logger.warning(f"Too many phones: {document.file_name}")
                    return
            except Exception as e:
                logger.error(f"Extract error {document.file_name}: {e}")
                return

            # Simpan metadata file
            try:
                txt_files = context.user_data.setdefault('txt_files_data', [])
                existing = [f['filename'] for f in txt_files]
                original = document.file_name
                filename = original
                c = 1
                while filename in existing:
                    name_part, ext_part = original.rsplit('.', 1)
                    filename = f"{name_part}_{c}.{ext_part}"
                    c += 1

                txt_files.append({
                    'filename': filename,
                    'original_filename': original,
                    'phone_numbers': phone_numbers,
                    'file_size': document.file_size or 0,
                    'processed_at': time.time()
                })

                context.user_data['chat_id'] = update.effective_chat.id
                context.user_data['last_file_at'] = time.time()

                # Preview live (tanpa tombol) + jadwalkan final preview
                now = time.time()
                last = context.user_data.get('last_preview_update', 0)
                if now - last > 2.0 or len(txt_files) == 1:
                    await self.show_files_preview(update, context, final=False)
                    context.user_data['last_preview_update'] = now

                self._schedule_final_preview(update, context)

                logger.info(f"Processed {filename}: phones={len(phone_numbers)} files={len(txt_files)}")
            except Exception as e:
                logger.error(f"Storage error {document.file_name}: {e}")
                return

        finally:
            if file_key in self.file_locks:
                del self.file_locks[file_key]

    # =========================
    # Preview & UI
    # =========================
    def _build_progress_line(self, cv_mode: str, file_count: int, final: bool) -> str:
        if final:
            return "✅ *Selesai membaca semua file.*"
        return "🔄 *Tunggu sebentar, bot sedang membaca file…*"

    async def show_files_preview(self, update, context, final: bool = False):
        try:
            txt_files = context.user_data.get('txt_files_data', [])
            cv_mode = context.user_data.get('cv_mode', 'v1')
            if not txt_files:
                return

            file_count = len(txt_files)
            total_phones = sum(len(f['phone_numbers']) for f in txt_files)

            header = "📤 *Ringkasan Upload*\n━━━━━━━━━━━━━━━━━━━━━━━\n"
            preview_text = header
            preview_text += "📁 *Daftar File:*\n"

            display_limit = 15
            display_files = txt_files[-display_limit:] if file_count > display_limit else txt_files
            start_idx = file_count - len(display_files) + 1
            for idx, f in enumerate(display_files, start=start_idx):
                preview_text += f"{idx}. `{f['filename']}` — 📞 {len(f['phone_numbers'])} nomor\n"

            status_line = self._build_progress_line(cv_mode, file_count, final)
            preview_text += f"{status_line}\n\n"

            if not final:
                pass

            preview_text += "━━━━━━━━━━━━━━━━━━━━━━━\n"
            preview_text += f"🧮 *Total:* {file_count} file · {total_phones} nomor"

            # === TOMBOL MUNCUL HANYA SAAT FINAL ===
            keyboard = None
            if final:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                if cv_mode == 'v2':
                    buttons = [[
                        InlineKeyboardButton("📄 FORMAT", callback_data='v2_format'),
                        InlineKeyboardButton("⌨️ INPUT",  callback_data='v2_input'),
                    ]]
                else:
                    buttons = [[
                        InlineKeyboardButton("🔹 Default", callback_data='output_default'),
                        InlineKeyboardButton("🎨 Custom", callback_data='output_custom')
                    ]]
                keyboard = InlineKeyboardMarkup(buttons)

            key = 'preview_message'
            if key in context.user_data and context.user_data[key]:
                try:
                    await context.user_data[key].edit_text(
                        preview_text,
                        reply_markup=keyboard,
                        parse_mode='Markdown'
                    )
                except Exception:
                    with contextlib.suppress(Exception):
                        await context.user_data[key].delete()
                    context.user_data[key] = await update.message.reply_text(
                        preview_text,
                        reply_markup=keyboard,
                        parse_mode='Markdown'
                    )
            else:
                context.user_data[key] = await update.message.reply_text(
                    preview_text,
                    reply_markup=keyboard,
                    parse_mode='Markdown'
                )

            if final:
                self._cancel_final_preview_task(context)
        except Exception as e:
            logger.error(f"Error showing files preview: {e}")
            with contextlib.suppress(Exception):
                file_count = len(context.user_data.get('txt_files_data', []))
                if file_count > 0:
                    await update.message.reply_text(f"✅ {file_count} file berhasil diproses!")

    # =========================
    # Mode V1: Default / Custom
    # =========================
    async def setup_default_output(self, query, context):
        try:
            context.user_data['output_mode'] = 'default'
            context.user_data['waiting_for_contact_name'] = True
            context.user_data['waiting_for_txt_files'] = False

            txt_files = context.user_data.get('txt_files_data', [])
            total_files = len(txt_files)
            total_phones = sum(len(f['phone_numbers']) for f in txt_files)

            summary = "\n".join([
                "🔧 *Mode Default dipilih*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"📁 *File:* {total_files}",
                f"📞 *Nomor:* {total_phones}",
                "📝 *Nama VCF* = sama dengan nama TXT",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "👤 *Ketik nama kontak:*",
            ])
            await query.edit_message_text(summary, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in setup_default_output: {e}")

    async def setup_custom_output(self, query, context):
        try:
            context.user_data['output_mode'] = 'custom'
            context.user_data['waiting_for_custom_filename'] = True
            context.user_data['waiting_for_txt_files'] = False

            txt_files = context.user_data.get('txt_files_data', [])
            total_files = len(txt_files)
            total_phones = sum(len(f['phone_numbers']) for f in txt_files)

            summary = "\n".join([
                "🎨 *Mode Custom dipilih*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"📁 *File:* {total_files}",
                f"📞 *Nomor:* {total_phones}",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "💡 *Contoh nama file:*",
                "• `pudidi1` → pudidi2.vcf, pudidi3.vcf, …",
                "• `kontak-5` → kontak-6.vcf, kontak-7.vcf, …",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "✏️ *Masukkan nama file yang diakhiri angka:*",
            ])
            await query.edit_message_text(summary, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in setup_custom_output: {e}")

    # =========================
    # V2 — Persiapan merged_phones
    # =========================
    async def _v2_prepare_single(self, query, context):
        """Siapkan merged_phones dari 1 file, lalu arahkan ke format/input sesuai pilihan."""
        try:
            context.user_data['waiting_for_txt_files'] = False
            txt_files = context.user_data.get('txt_files_data', [])
            if not txt_files:
                await query.edit_message_text("❌ Tidak ada file yang diproses.")
                return
            context.user_data['merged_phones'] = txt_files[0]['phone_numbers']
            await self._v2_next_step_after_prepare(query, context, len(txt_files[0]['phone_numbers']))
        except Exception as e:
            logger.error(f"Error in _v2_prepare_single: {e}")

    async def _v2_prepare_batch(self, query, context):
        """Gabung nomor dari banyak file, normalisasi & dedup."""
        try:
            context.user_data['waiting_for_txt_files'] = False

            txt_files = context.user_data.get('txt_files_data', [])
            all_phones = []
            for f in txt_files:
                all_phones.extend(f['phone_numbers'])

            # Normalisasi + dedup global
            all_phones = normalize_phone_list_format(all_phones)
            seen = set()
            unique = []
            for p in all_phones:
                if p not in seen:
                    seen.add(p)
                    unique.append(p)

            context.user_data['merged_phones'] = unique
            await self._v2_next_step_after_prepare(query, context, len(unique))
        except Exception as e:
            logger.error(f"Error in _v2_prepare_batch: {e}")

    async def _v2_next_step_after_prepare(self, query, context, total_phones: int):
        """Lanjut ke MODE FORMAT atau MODE INPUT sesuai pilihan."""
        target = context.user_data.get('v2_target_mode', 'format')
        if target == 'input':
            await self._v2_start_input_wizard(query, context, total_phones)
        else:
            await self._v2_show_format_prompt(query, context, total_phones)

    # =========================
    # V2 — MODE FORMAT (5 parameter, dipisah koma)
    # =========================
    async def _v2_show_format_prompt(self, query, context, total_phones):
        """
        FORMAT BATCH (5 parameter):
        nama_kontak, nama_file, nomor_per_file, jumlah_file, start_num
        """
        try:
            context.user_data['waiting_for_v2_format'] = True
            text = "\n".join([
                "📄 *MODE FORMAT — V2*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"📞 *{total_phones} nomor unik* siap diproses",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "Ketik *5 parameter* dipisah koma:",
                "`nama_kontak, nama_file, nomor_per_file, jumlah_file, start_num`",
                "",
                "Contoh:",
                "`Admin, kontak, 50, 10, 5`",
                "",
                "*Hasil:*",
                "`kontak5.vcf … kontak14.vcf` (nama file mulai dari 5)",
                "Penomoran *nama kontak* tetap global & berurutan.",
            ])
            await query.edit_message_text(text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in _v2_show_format_prompt: {e}")

    async def process_v2_format_input(self, update, context, user_input):
        """Parse 5 parameter & jalankan pembagian + kirim file (nama file mulai start_num)."""
        try:
            parts = [p.strip() for p in user_input.split(',')]
            if len(parts) != 5:
                await update.message.reply_text(
                    "❌ Format salah! Harus *5 parameter*.\n\n"
                    "Contoh: `Admin, kontak, 50, 10, 5`",
                    parse_mode='Markdown'
                )
                return

            contact_name, file_base, contacts_per_file, total_files, start_num = parts
            try:
                contacts_per_file = int(contacts_per_file)
                total_files = int(total_files)
                start_num = int(start_num)
            except ValueError:
                await update.message.reply_text("❌ Parameter angka tidak valid!", parse_mode='Markdown')
                return

            if contacts_per_file <= 0 or total_files <= 0 or start_num <= 0:
                await update.message.reply_text("❌ Semua angka harus > 0.", parse_mode='Markdown')
                return

            phones = context.user_data.get('merged_phones', [])
            total_needed = contacts_per_file * total_files
            if len(phones) < total_needed:
                await update.message.reply_text(
                    f"❌ Tidak cukup nomor!\n\nTersedia: {len(phones)} · Dibutuhkan: {total_needed}",
                    parse_mode='Markdown'
                )
                return

            # Bagi jadi batch
            phone_batches = split_phones_into_batches(phones, contacts_per_file, total_files)

            start_time = time.time()
            progress_msg = await update.message.reply_text("🔄 Menyiapkan file…")
            successful_files = 0
            total_processed = 0
            global_idx = 1  # penomoran *nama kontak* global (1..N)

            for i, batch in enumerate(phone_batches):
                try:
                    filename = f"{file_base}{start_num + i}.vcf"
                    vcf_content = create_vcf_from_phones(batch, contact_name, start_index=global_idx)
                    if vcf_content:
                        await self._progress_edit(progress_msg, i + 1, len(phone_batches), "Mengirim file")
                        await send_vcf_file(update, filename, vcf_content)
                        successful_files += 1
                        total_processed += len(batch)
                        global_idx += len(batch)
                        await asyncio.sleep(SLEEP_BETWEEN_FILES)
                except Exception as e:
                    logger.error(f"Batch {i} error: {e}")
                    continue

            with contextlib.suppress(Exception):
                await progress_msg.delete()

            dur = time.time() - start_time
            end_num = start_num + successful_files - 1
            parts = [
                "✅ *V2 (FORMAT) Selesai*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"📁 *File berhasil:* {successful_files}",
                f"👤 *Nama kontak:* {contact_name}",
                f"📞 *Total kontak:* {total_processed}",
                f"📝 *Range nama file:* {file_base}{start_num}.vcf … {file_base}{end_num}.vcf",
                "▫️ Penomoran *nama kontak* global berurutan",
                f"⏱ *Waktu proses:* {dur:.2f} detik",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "Gunakan /start untuk memulai baru."
            ]
            await update.message.reply_text("\n".join(parts), parse_mode='Markdown')
            context.user_data.clear()

        except Exception as e:
            logger.error(f"Error in V2 FORMAT processing: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan saat memproses V2 (FORMAT).")
            context.user_data.clear()

    # =========================
    # V2 — MODE INPUT (wizard 1‑1, 5 langkah)
    # =========================
    async def _v2_start_input_wizard(self, query, context, total_phones):
        try:
            context.user_data.update({
                'v2_input_step': 1,
                'v2_params': {},
                'waiting_for_v2_format': False  # pastikan mati
            })
            text = (
                "⌨️ *MODE INPUT — V2*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 *{total_phones} nomor unik* siap diproses\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Langkah 1/5 — *Nama kontak*\n"
                "📝 Ketik *nama kontak* untuk VCF."
            )
            await query.edit_message_text(text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error in _v2_start_input_wizard: {e}")

    async def _v2_wizard_next_prompt(self, update, context):
        step = context.user_data.get('v2_input_step', 1)
        total_phones = len(context.user_data.get('merged_phones', []))

        if step == 1:
            context.user_data['v2_input_step'] = 2
            await update.message.reply_text(
                "Langkah 2/5 — *Nama file dasar* (tanpa *.vcf*).\n"
                "🧾 Contoh: `kontak`, `batch-`, `data_`",
                parse_mode='Markdown'
            )
        elif step == 2:
            context.user_data['v2_input_step'] = 3
            await update.message.reply_text(
                "Langkah 3/5 — *Jumlah nomor per file* (angka > 0).\n"
                f"ℹ️ *Total tersedia:* {total_phones} nomor.",
                parse_mode='Markdown'
            )
        elif step == 3:
            context.user_data['v2_input_step'] = 4
            await update.message.reply_text(
                "Langkah 4/5 — *Jumlah file* (angka > 0).",
                parse_mode='Markdown'
            )
        elif step == 4:
            context.user_data['v2_input_step'] = 5
            await update.message.reply_text(
                "Langkah 5/5 — *Start urutan angka nama file* (angka > 0).\n"
                "🧾 Contoh: 5 → hasil `nama5.vcf, nama6.vcf, …`",
                parse_mode='Markdown'
            )
        # step 5 selesai → proses di handler

    # =========================
    # Handler input teks (semua mode)
    # =========================
    async def handle_text_input(self, update, context):
        try:
            if any([
                context.user_data.get('waiting_for_v2_format'),
                context.user_data.get('waiting_for_contact_name'),
                context.user_data.get('waiting_for_custom_filename'),
                context.user_data.get('waiting_for_custom_contact_name')
            ]):
                user_input = (update.message.text or "").strip()

                if context.user_data.get('waiting_for_v2_format'):
                    await self.process_v2_format_input(update, context, user_input)
                    return
                if context.user_data.get('waiting_for_contact_name'):
                    await self.process_default_mode(update, context, user_input); return
                if context.user_data.get('waiting_for_custom_filename'):
                    await self.process_custom_filename_input(update, context, user_input); return
                if context.user_data.get('waiting_for_custom_contact_name'):
                    await self.process_custom_contact_input(update, context, user_input); return

            # === V2 wizard (INPUT) ===
            if context.user_data.get('v2_input_step'):
                step = context.user_data['v2_input_step']
                txt = (update.message.text or "").strip()
                params = context.user_data.setdefault('v2_params', {})

                if step == 1:
                    params['contact_name'] = txt or "Kontak"
                    await self._v2_wizard_next_prompt(update, context)
                    return
                if step == 2:
                    params['file_base'] = txt or "kontak"
                    await self._v2_wizard_next_prompt(update, context)
                    return
                if step == 3:
                    try:
                        n = int(txt)
                        if n <= 0: raise ValueError()
                        params['contacts_per_file'] = n
                    except Exception:
                        await update.message.reply_text("❌ Masukkan angka valid (> 0)."); return
                    await self._v2_wizard_next_prompt(update, context)
                    return
                if step == 4:
                    try:
                        n = int(txt)
                        if n <= 0: raise ValueError()
                        params['total_files'] = n
                    except Exception:
                        await update.message.reply_text("❌ Masukkan angka valid (> 0)."); return
                    await self._v2_wizard_next_prompt(update, context)
                    return
                if step == 5:
                    try:
                        n = int(txt)
                        if n <= 0: raise ValueError()
                        params['start_num'] = n
                    except Exception:
                        await update.message.reply_text("❌ Masukkan angka valid (> 0)."); return

                    # semua lengkap → jalankan proses
                    await self._v2_run_after_collect(update, context, params)
                    return

        except Exception as e:
            logger.error(f"Error handling text input: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan saat memproses input.")

    async def _progress_edit(self, msg, cur, total, phase="Memproses"):
        try:
            pct = int((cur / max(total, 1)) * 100)
            await msg.edit_text(f"🔄 {phase} {cur}/{total} ({pct}%)…")
        except Exception:
            pass

    async def _v2_run_after_collect(self, update, context, params):
        """Jalankan eksekusi V2 berdasarkan params hasil wizard INPUT (5 langkah)."""
        try:
            contact_name = params['contact_name']
            file_base = params['file_base']
            contacts_per_file = params['contacts_per_file']
            total_files = params['total_files']
            start_num = params['start_num']

            phones = context.user_data.get('merged_phones', [])
            total_needed = contacts_per_file * total_files
            if len(phones) < total_needed:
                await update.message.reply_text(
                    f"❌ Tidak cukup nomor!\n\nTersedia: {len(phones)} · Dibutuhkan: {total_needed}",
                    parse_mode='Markdown'
                )
                return

            phone_batches = split_phones_into_batches(phones, contacts_per_file, total_files)

            start_time = time.time()
            progress_msg = await update.message.reply_text("🔄 Menyiapkan file…")
            successful_files = 0
            total_processed = 0
            global_idx = 1  # penomoran *nama kontak* global mulai 1

            for i, batch in enumerate(phone_batches):
                try:
                    filename = f"{file_base}{start_num + i}.vcf"
                    vcf_content = create_vcf_from_phones(batch, contact_name, start_index=global_idx)
                    if vcf_content:
                        await self._progress_edit(progress_msg, i + 1, len(phone_batches), "Mengirim file")
                        await send_vcf_file(update, filename, vcf_content)
                        successful_files += 1
                        total_processed += len(batch)
                        global_idx += len(batch)
                        await asyncio.sleep(SLEEP_BETWEEN_FILES)
                except Exception as e:
                    logger.error(f"Batch {i} error: {e}")
                    continue

            with contextlib.suppress(Exception):
                await progress_msg.delete()

            dur = time.time() - start_time
            end_num = start_num + successful_files - 1
            parts = [
                "✅ *V2 (INPUT) Selesai*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"📁 *File berhasil:* {successful_files}",
                f"👤 *Nama kontak:* {contact_name}",
                f"📞 *Total kontak:* {total_processed}",
                f"📝 *Range nama file:* {file_base}{start_num}.vcf … {file_base}{end_num}.vcf",
                "▫️ Penomoran *nama kontak* global berurutan",
                f"⏱ *Waktu proses:* {dur:.2f} detik",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "Gunakan /start untuk memulai baru."
            ]
            await update.message.reply_text("\n".join(parts), parse_mode='Markdown')
            context.user_data.clear()

        except Exception as e:
            logger.error(f"Error in V2 INPUT processing: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan saat memproses V2 (INPUT).")
            context.user_data.clear()

    # =========================
    # V1: proses existing (tidak diubah)
    # =========================
    async def process_default_mode(self, update, context, contact_name):
        from utils import clean_name_for_vcf
        try:
            contact_name = clean_name_for_vcf(contact_name)
            if not contact_name:
                await update.message.reply_text("❌ Nama kontak tidak valid!")
                return

            txt_files = context.user_data.get('txt_files_data', [])
            successful_files = 0
            total_processed = 0
            failed = []
            global_idx = 1

            start_time = time.time()
            progress_msg = await update.message.reply_text("🔄 Menyiapkan file…")

            for idx, f in enumerate(txt_files, 1):
                try:
                    filename = f['filename'].rsplit('.txt', 1)[0] + '.vcf'
                    normalized = normalize_phone_list_format(f['phone_numbers'])
                    await self._progress_edit(progress_msg, idx, len(txt_files), "Mengirim file")
                    vcf_content = create_vcf_from_phones(normalized, contact_name, start_index=global_idx)
                    if vcf_content:
                        await send_vcf_file(update, filename, vcf_content)
                        successful_files += 1
                        total_processed += len(normalized)
                        global_idx += len(normalized)
                        await asyncio.sleep(SLEEP_BETWEEN_FILES)
                    else:
                        failed.append(f['filename'])
                except Exception as e:
                    logger.error(f"Error processing {f['filename']}: {e}")
                    failed.append(f['filename'])

            with contextlib.suppress(Exception):
                await progress_msg.delete()

            dur = time.time() - start_time
            parts = [
                "✅ *V1 Default Selesai*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"📁 *File berhasil:* {successful_files}",
                f"👤 *Nama kontak:* {contact_name}",
                f"📞 *Total kontak:* {total_processed}",
            ]
            if failed:
                parts.append(f"❌ *Gagal:* {len(failed)} file")
            parts.extend([
                f"⏱ *Waktu proses:* {dur:.2f} detik",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "Gunakan /start untuk memulai baru."
            ])
            await update.message.reply_text("\n".join(parts), parse_mode='Markdown')
            context.user_data.clear()

        except Exception as e:
            logger.error(f"Error processing default mode: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan saat memproses file.")
            context.user_data.clear()

    async def process_custom_filename_input(self, update, context, base_filename):
        import re
        try:
            if not re.search(r'\d+$', base_filename):
                await update.message.reply_text(
                    "❌ Nama dasar harus diakhiri angka.\nContoh: `kontak1` atau `data-5`"
                )
                return

            txt_files = context.user_data.get('txt_files_data', [])
            total_files = len(txt_files)

            custom_filenames = generate_custom_filenames(base_filename, total_files)
            if not custom_filenames:
                await update.message.reply_text("❌ Gagal membuat pattern nama file.")
                return

            context.user_data['custom_filenames'] = custom_filenames
            context.user_data['waiting_for_custom_filename'] = False
            context.user_data['waiting_for_custom_contact_name'] = True

            total_phones = sum(len(f['phone_numbers']) for f in txt_files)
            preview = "\n".join([
                "🎨 *Pattern Custom diset!*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"📁 *File:* {total_files}",
                f"📞 *Nomor:* {total_phones}",
                f"📝 *Range nama:* {custom_filenames[0]} … {custom_filenames[-1]}",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "👤 *Ketik nama kontak:*",
            ])
            await update.message.reply_text(preview, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Error generating custom filenames: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan saat membuat pattern.")
            context.user_data.clear()

    async def process_custom_contact_input(self, update, context, contact_name):
        from utils import clean_name_for_vcf
        try:
            contact_name = clean_name_for_vcf(contact_name)
            if not contact_name:
                await update.message.reply_text("❌ Nama kontak tidak valid!")
                return

            txt_files = context.user_data.get('txt_files_data', [])
            custom = context.user_data.get('custom_filenames', [])

            successful_files = 0
            total_processed = 0
            failed = []
            global_idx = 1

            start_time = time.time()
            progress_msg = await update.message.reply_text("🔄 Menyiapkan file…")

            for i, f in enumerate(txt_files):
                try:
                    if i < len(custom):
                        filename = custom[i]
                        normalized = normalize_phone_list_format(f['phone_numbers'])
                        await self._progress_edit(progress_msg, i + 1, len(txt_files), "Mengirim file")
                        vcf_content = create_vcf_from_phones(normalized, contact_name, start_index=global_idx)
                        if vcf_content:
                            await send_vcf_file(update, filename, vcf_content)
                            successful_files += 1
                            total_processed += len(normalized)
                            global_idx += len(normalized)
                            await asyncio.sleep(SLEEP_BETWEEN_FILES)
                        else:
                            failed.append(f['filename'])
                except Exception as e:
                    logger.error(f"Error processing custom {f['filename']}: {e}")
                    failed.append(f['filename'])

            with contextlib.suppress(Exception):
                await progress_msg.delete()

            dur = time.time() - start_time
            parts = [
                "✅ *V1 Custom Selesai*",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                f"📁 *File berhasil:* {successful_files}",
                f"👤 *Nama kontak:* {contact_name}",
                f"📞 *Total kontak:* {total_processed}",
                "🎨 *Pattern custom diterapkan (penomoran global)*",
            ]
            if failed:
                parts.append(f"❌ *Gagal:* {len(failed)} file")
            parts.extend([
                f"⏱ *Waktu proses:* {dur:.2f} detik",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "Gunakan /start untuk memulai baru."
            ])
            await update.message.reply_text("\n".join(parts), parse_mode='Markdown')
            context.user_data.clear()

        except Exception as e:
            logger.error(f"Error in custom mode: {e}")
            await update.message.reply_text("❌ Terjadi kesalahan saat memproses custom mode.")
            context.user_data.clear()
