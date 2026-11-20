import asyncio
import logging
import os

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv


# =========================
# CONFIG
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "0")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 0

DB_NAME = "music.db"

if not BOT_TOKEN or not ADMIN_ID:
    raise RuntimeError(
        "Iltimos, .env faylida BOT_TOKEN va ADMIN_ID qiymatlarini kiriting."
    )


# =========================
# BOT VA FSM
# =========================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class UploadSong(StatesGroup):
    audio = State()
    title = State()
    artist = State()


class SearchSong(StatesGroup):
    query = State()


# =========================
# KEYBOARDS
# =========================

def main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [
            KeyboardButton(text="🔎 Qo'shiq qidirish"),
            KeyboardButton(text="📤 Qo'shiq yuklash"),
        ],
        [
            KeyboardButton(text="⭐ Sevimlilar"),
            KeyboardButton(text="ℹ️ Bot haqida"),
        ],
    ]
    if is_admin:
        rows.insert(0, [KeyboardButton(text="🛡 Admin panel")])

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def song_keyboard(song_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="📥 Yuklab olish",
                callback_data=f"download:{song_id}",
            )],
            [InlineKeyboardButton(
                text="⭐ Sevimlilarga qo'shish",
                callback_data=f"fav:{song_id}",
            )],
        ]
    )


def favorite_keyboard(song_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Yuklab olish",
                    callback_data=f"download:{song_id}",
                ),
                InlineKeyboardButton(
                    text="🗑 O'chirish",
                    callback_data=f"unfav:{song_id}",
                ),
            ]
        ]
    )


def admin_keyboard(song_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Tasdiqlash",
                    callback_data=f"approve:{song_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Rad etish",
                    callback_data=f"reject:{song_id}",
                ),
            ]
        ]
    )


# =========================
# DATABASE
# =========================

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                uploader_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                downloads INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                song_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, song_id)
            )
        """)
        await db.commit()


async def save_user(user):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
        """, (user.id, user.username, user.full_name))
        await db.commit()


async def add_song(file_id: str, title: str, artist: str, uploader_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            INSERT INTO songs (file_id, title, artist, uploader_id)
            VALUES (?, ?, ?, ?)
        """, (file_id, title, artist, uploader_id))
        await db.commit()
        return cursor.lastrowid


async def get_song(song_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM songs
            WHERE id = ? AND status = 'approved'
        """, (song_id,))
        return await cursor.fetchone()


async def search_songs(query: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM songs
            WHERE status = 'approved'
              AND (title LIKE ? OR artist LIKE ?)
            ORDER BY downloads DESC, id DESC
            LIMIT 20
        """, (f"%{query}%", f"%{query}%"))
        return await cursor.fetchall()


async def add_favorite(user_id: int, song_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR IGNORE INTO favorites (user_id, song_id)
            VALUES (?, ?)
        """, (user_id, song_id))
        await db.commit()


async def remove_favorite(user_id: int, song_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            DELETE FROM favorites
            WHERE user_id = ? AND song_id = ?
        """, (user_id, song_id))
        await db.commit()


async def get_favorites(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT songs.*
            FROM songs
            INNER JOIN favorites ON favorites.song_id = songs.id
            WHERE favorites.user_id = ?
              AND songs.status = 'approved'
            ORDER BY songs.id DESC
        """, (user_id,))
        return await cursor.fetchall()


async def increment_download(song_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE songs SET downloads = downloads + 1
            WHERE id = ? AND status = 'approved'
        """, (song_id,))
        await db.commit()


async def get_pending_songs():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM songs
            WHERE status = 'pending'
            ORDER BY id DESC
        """)
        return await cursor.fetchall()


async def set_song_status(song_id: int, status: str) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            UPDATE songs SET status = ?
            WHERE id = ? AND status = 'pending'
        """, (status, song_id))
        await db.commit()
        return cursor.rowcount > 0


async def get_statistics():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT
                (SELECT COUNT(*) FROM users),
                (SELECT COUNT(*) FROM songs WHERE status = 'approved'),
                (SELECT COUNT(*) FROM songs WHERE status = 'pending'),
                (SELECT COALESCE(SUM(downloads), 0)
                 FROM songs WHERE status = 'approved')
        """)
        return await cursor.fetchone()


async def get_uploader_id(song_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT uploader_id FROM songs WHERE id = ?",
            (song_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


# =========================
# START / COMMON COMMANDS
# =========================

@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await save_user(message.from_user)
    await message.answer(
        "🎵 MUSIC LIBRARY BOT\n\n"
        "Qo'shiqlarni qidiring, MP3 yuboring va sevimlilaringizga saqlang.\n"
        "Yuborilgan qo'shiqlar admin tekshiruvidan keyin kutubxonaga qo'shiladi.",
        reply_markup=main_keyboard(message.from_user.id == ADMIN_ID),
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Amal bekor qilindi.",
        reply_markup=main_keyboard(message.from_user.id == ADMIN_ID),
    )


@dp.message(F.text == "ℹ️ Bot haqida")
async def about(message: Message):
    await message.answer(
        "🎵 MUSIC LIBRARY\n\n"
        "🔎 Qo'shiq nomi yoki xonanda bo'yicha qidiring.\n"
        "📤 Tarqatish huquqiga ega bo'lgan MP3 faylingizni yuboring.\n"
        "⭐ Yoqtirgan qo'shiqlaringizni saqlang.\n"
        "📥 Tasdiqlangan qo'shiqlarni yuklab oling."
    )


# =========================
# UPLOAD SONG
# =========================

@dp.message(F.text == "📤 Qo'shiq yuklash")
async def upload_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(UploadSong.audio)
    await message.answer(
        "📤 MP3 qo'shiqni audio yoki fayl ko'rinishida yuboring.\n"
        "Faqat tarqatish huquqiga ega bo'lgan fayllarni yuboring.\n\n"
        "Bekor qilish: /cancel"
    )


async def accept_mp3(message: Message, state: FSMContext, file_id: str):
    await state.update_data(file_id=file_id)
    await state.set_state(UploadSong.title)
    await message.answer("🎵 Qo'shiq nomini yozing:")


@dp.message(UploadSong.audio, F.audio)
async def receive_audio(message: Message, state: FSMContext):
    audio = message.audio
    is_mp3 = (
        audio.mime_type == "audio/mpeg"
        or (audio.file_name and audio.file_name.lower().endswith(".mp3"))
    )
    if not is_mp3:
        await message.answer("❌ MP3 formatidagi audio yuboring.")
        return
    await accept_mp3(message, state, audio.file_id)


@dp.message(UploadSong.audio, F.document)
async def receive_document(message: Message, state: FSMContext):
    document = message.document
    is_mp3 = (
        document.mime_type == "audio/mpeg"
        or (
            document.file_name
            and document.file_name.lower().endswith(".mp3")
        )
    )
    if not is_mp3:
        await message.answer("❌ Faqat MP3 fayl qabul qilinadi.")
        return
    await accept_mp3(message, state, document.file_id)


@dp.message(UploadSong.audio)
async def wrong_upload_type(message: Message):
    await message.answer("Iltimos, MP3 audio yoki MP3 fayl yuboring.")


@dp.message(UploadSong.title, F.text)
async def receive_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not title or len(title) > 150:
        await message.answer("❌ Qo'shiq nomi 1–150 belgi bo'lishi kerak.")
        return
    await state.update_data(title=title)
    await state.set_state(UploadSong.artist)
    await message.answer("🎤 Qo'shiqni ijro etgan xonanda nomini yozing:")


@dp.message(UploadSong.artist, F.text)
async def receive_artist(message: Message, state: FSMContext):
    artist = message.text.strip()
    if not artist or len(artist) > 150:
        await message.answer("❌ Xonanda nomi 1–150 belgi bo'lishi kerak.")
        return

    data = await state.get_data()
    song_id = await add_song(
        file_id=data["file_id"],
        title=data["title"],
        artist=artist,
        uploader_id=message.from_user.id,
    )
    await state.clear()

    await message.answer(
        f"✅ Qo'shiq admin tekshiruviga yuborildi!\n"
        f"🆔 Ariza raqami: {song_id}"
    )

    caption = (
        f"🎵 YANGI QO'SHIQ\n\n"
        f"🆔 ID: {song_id}\n"
        f"🎶 Nomi: {data['title']}\n"
        f"🎤 Xonanda: {artist}\n"
        f"👤 Yuklagan: {message.from_user.full_name}\n"
        f"🆔 User ID: {message.from_user.id}"
    )

    try:
        await bot.send_audio(
            chat_id=ADMIN_ID,
            audio=data["file_id"],
            caption=caption,
            reply_markup=admin_keyboard(song_id),
        )
    except Exception:
        logging.exception("Admin uchun audio xabar yuborilmadi")
        await message.answer(
            "⚠️ Ariza bazaga saqlandi, lekin admin xabarini yuborib bo'lmadi. "
            "Admin botga /pending yuborib tekshirishi mumkin."
        )


@dp.message(UploadSong.title)
@dp.message(UploadSong.artist)
async def wrong_metadata_type(message: Message):
    await message.answer("Iltimos, javobni matn ko'rinishida yuboring.")


# =========================
# SEARCH
# =========================

@dp.message(F.text == "🔎 Qo'shiq qidirish")
async def search_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(SearchSong.query)
    await message.answer(
        "🔎 Qo'shiq nomi yoki xonanda nomini yozing:\n"
        "Bekor qilish: /cancel"
    )


@dp.message(SearchSong.query, F.text)
async def search_result(message: Message, state: FSMContext):
    query = message.text.strip()
    if not query:
        await message.answer("Qidiruv so'zini kiriting.")
        return

    songs = await search_songs(query)
    await state.clear()

    if not songs:
        await message.answer("😔 Hech qanday qo'shiq topilmadi.")
        return

    await message.answer(f"🔎 Topilgan natijalar: {len(songs)} ta")
    for song in songs:
        caption = (
            f"🎵 {song['title']}\n"
            f"🎤 Xonanda: {song['artist']}\n"
            f"📥 Yuklab olingan: {song['downloads']} marta\n"
            f"🆔 Qo'shiq ID: {song['id']}"
        )
        await message.answer(
            caption,
            reply_markup=song_keyboard(song["id"]),
        )


@dp.message(SearchSong.query)
async def search_non_text(message: Message):
    await message.answer("Qidirish uchun qo'shiq yoki xonanda nomini matn bilan yozing.")


# =========================
# DOWNLOAD
# =========================

@dp.callback_query(F.data.startswith("download:"))
async def download_song(callback: CallbackQuery):
    try:
        song_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri qo'shiq ID.", show_alert=True)
        return

    song = await get_song(song_id)
    if not song:
        await callback.answer("Qo'shiq topilmadi yoki mavjud emas.", show_alert=True)
        return

    try:
        await bot.send_audio(
            chat_id=callback.from_user.id,
            audio=song["file_id"],
            caption=f"🎵 {song['title']}\n🎤 {song['artist']}",
        )
        await increment_download(song_id)
        await callback.answer("📥 Qo'shiq shaxsiy xabaringizga yuborildi.")
    except Exception:
        logging.exception("Audio yuborishda xatolik")
        await callback.answer(
            "Bot sizga xabar yubora olmadi. Avval botga /start bosing.",
            show_alert=True,
        )


# =========================
# FAVORITES
# =========================

@dp.callback_query(F.data.startswith("fav:"))
async def favorite_add(callback: CallbackQuery):
    try:
        song_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri qo'shiq ID.", show_alert=True)
        return

    song = await get_song(song_id)
    if not song:
        await callback.answer("Qo'shiq topilmadi.", show_alert=True)
        return

    await add_favorite(callback.from_user.id, song_id)
    await callback.answer("⭐ Sevimlilarga qo'shildi!", show_alert=True)


@dp.callback_query(F.data.startswith("unfav:"))
async def favorite_remove(callback: CallbackQuery):
    try:
        song_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri qo'shiq ID.", show_alert=True)
        return

    await remove_favorite(callback.from_user.id, song_id)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("🗑 Sevimlilardan o'chirildi!")


@dp.message(F.text == "⭐ Sevimlilar")
async def favorites_list(message: Message):
    songs = await get_favorites(message.from_user.id)
    if not songs:
        await message.answer("⭐ Sevimlilar ro'yxatingiz hozircha bo'sh.")
        return

    await message.answer(f"⭐ Sevimli qo'shiqlaringiz: {len(songs)} ta")
    for song in songs:
        await message.answer(
            f"🎵 {song['title']}\n"
            f"🎤 {song['artist']}\n"
            f"📥 Yuklab olingan: {song['downloads']} marta",
            reply_markup=favorite_keyboard(song["id"]),
        )


# =========================
# ADMIN PANEL
# =========================

@dp.message(F.text == "🛡 Admin panel")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    users, approved, pending, downloads = await get_statistics()
    await message.answer(
        "🛡 ADMIN PANEL\n\n"
        f"👥 Foydalanuvchilar: {users}\n"
        f"🎵 Tasdiqlangan qo'shiqlar: {approved}\n"
        f"⏳ Kutilayotgan qo'shiqlar: {pending}\n"
        f"📥 Jami yuklab olishlar: {downloads}\n\n"
        "Buyruqlar:\n"
        "/pending — tekshirilmagan qo'shiqlar"
    )


@dp.message(Command("pending"))
async def pending_songs(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    songs = await get_pending_songs()
    if not songs:
        await message.answer("⏳ Tekshirilishi kerak bo'lgan qo'shiqlar yo'q.")
        return

    await message.answer(f"⏳ Kutilayotgan qo'shiqlar: {len(songs)} ta")
    for song in songs:
        caption = (
            f"🎵 {song['title']}\n"
            f"🎤 {song['artist']}\n"
            f"🆔 ID: {song['id']}\n"
            f"👤 Yuklagan foydalanuvchi ID: {song['uploader_id']}"
        )
        try:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=song["file_id"],
                caption=caption,
                reply_markup=admin_keyboard(song["id"]),
            )
        except Exception:
            logging.exception("Pending audio yuborilmadi: %s", song["id"])
            await message.answer(
                f"⚠️ #{song['id']} qo'shig'ini ko'rsatib bo'lmadi."
            )


@dp.callback_query(F.data.startswith("approve:"))
async def approve_song_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Sizda admin ruxsati yo'q.", show_alert=True)
        return

    try:
        song_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ID.", show_alert=True)
        return

    success = await set_song_status(song_id, "approved")
    if not success:
        await callback.answer(
            "Qo'shiq topilmadi yoki oldin ko'rib chiqilgan.",
            show_alert=True,
        )
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"✅ #{song_id} qo'shiq tasdiqlandi.")
    await callback.answer("Tasdiqlandi!")

    uploader_id = await get_uploader_id(song_id)
    if uploader_id:
        try:
            await bot.send_message(
                uploader_id,
                f"🎉 Siz yuborgan #{song_id} qo'shiq tasdiqlandi!"
            )
        except Exception:
            logging.info("Yuklagan foydalanuvchiga xabar yuborilmadi.")


@dp.callback_query(F.data.startswith("reject:"))
async def reject_song_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Sizda admin ruxsati yo'q.", show_alert=True)
        return

    try:
        song_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Noto'g'ri ID.", show_alert=True)
        return

    success = await set_song_status(song_id, "rejected")
    if not success:
        await callback.answer(
            "Qo'shiq topilmadi yoki oldin ko'rib chiqilgan.",
            show_alert=True,
        )
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"❌ #{song_id} qo'shiq rad etildi.")
    await callback.answer("Rad etildi!")

    uploader_id = await get_uploader_id(song_id)
    if uploader_id:
        try:
            await bot.send_message(
                uploader_id,
                f"❌ Siz yuborgan #{song_id} qo'shiq rad etildi."
            )
        except Exception:
            logging.info("Yuklagan foydalanuvchiga xabar yuborilmadi.")


# =========================
# MAIN
# =========================

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    logging.info("Music Library bot ishga tushdi.")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
