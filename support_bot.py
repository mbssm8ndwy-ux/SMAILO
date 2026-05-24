from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

import config

SUPPORT_BOT_TOKEN = "8878588955:AAEFxn-Mm2-Qc8HnKnyVzBgQKmIR3WkAYvw"

session = AiohttpSession(proxy=config.PROXY_URL) if getattr(config, "PROXY_URL", "").strip() else None
bot = Bot(token=SUPPORT_BOT_TOKEN, session=session) if session else Bot(token=SUPPORT_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

LOG_CHAT_ID = int(config.LOG_CHAT_ID)

# log_message_id -> user_id
_ticket_map: dict[int, int] = {}


@dp.message(F.chat.type == "private")
async def user_to_log(message: Message):
    user = message.from_user
    cid = LOG_CHAT_ID
    if not cid:
        await message.answer("Поддержка временно недоступна.")
        return

    uname = f"@{user.username}" if user.username else "без username"
    body = message.text or message.caption or "(без текста)"

    text = (
        f"🆘 Обращение в поддержку\n"
        f"От: {user.full_name} ({uname})\n"
        f"ID: {user.id}\n\n"
        f"{body}\n\n"
        "↩️ Ответьте реплаем — ответ уйдёт пользователю."
    )

    if message.photo:
        sent = await bot.send_photo(cid, message.photo[-1].file_id, caption=text)
    elif message.video:
        sent = await bot.send_video(cid, message.video.file_id, caption=text)
    elif message.document:
        sent = await bot.send_document(cid, message.document.file_id, caption=text)
    elif message.voice:
        sent = await bot.send_voice(cid, message.voice.file_id, caption=text)
    elif message.video_note:
        sent = await bot.send_video_note(cid, message.video_note.file_id)
        await bot.send_message(cid, text)
    elif message.sticker:
        sent = await bot.send_sticker(cid, message.sticker.file_id)
        await bot.send_message(cid, text)
    else:
        sent = await bot.send_message(cid, text)

    _ticket_map[sent.message_id] = user.id
    await message.answer("✅ Сообщение отправлено в поддержку. Ответ придёт сюда.")


@dp.message(F.chat.id == LOG_CHAT_ID, F.reply_to_message)
async def log_reply_to_user(message: Message):
    replied_id = message.reply_to_message.message_id
    user_id = _ticket_map.get(replied_id)
    if not user_id:
        await message.reply("❌ Пользователь не найден (тикет сброшен при перезапуске).")
        return
    text = message.text or message.caption or ""
    if not text:
        await message.reply("❌ Пустой ответ.")
        return
    try:
        if message.photo:
            await bot.send_photo(user_id, message.photo[-1].file_id, caption=f"💬 Ответ поддержки:\n\n{text}")
        elif message.video:
            await bot.send_video(user_id, message.video.file_id, caption=f"💬 Ответ поддержки:\n\n{text}")
        else:
            await bot.send_message(user_id, f"💬 Ответ поддержки:\n\n{text}")
        await message.reply("✅ Ответ отправлен.")
    except Exception as e:
        await message.reply(f"❌ Ошибка: {e}")


async def run_support_bot():
    me = await bot.get_me()
    logging.info("Support bot started: @%s", me.username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
