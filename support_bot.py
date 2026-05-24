from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

import config

SUPPORT_BOT_TOKEN = "8878588955:AAEFxn-Mm2-Qc8HnKnyVzBgQKmIR3WkAYvw"
_LOG_CHAT_ID = int(config.LOG_CHAT_ID)

# ticket_id (log msg id) -> user_id
_ticket_map: dict[int, int] = {}

session = AiohttpSession(proxy=config.PROXY_URL) if getattr(config, "PROXY_URL", "").strip() else None
bot = Bot(token=SUPPORT_BOT_TOKEN, session=session) if session else Bot(token=SUPPORT_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

def _get_forward_text(message: Message) -> str:
    user = message.from_user
    uname = f"@{user.username}" if user.username else "без username"
    text = (message.text or message.caption or "").strip() or "(без текста)"
    caption = message.caption or ""
    body = caption + "\n\n" + message.text if message.caption and message.text else (message.text or message.caption or "(медиа)")
    return (
        f"🆘 Обращение в поддержку\n"
        f"От: {user.full_name} ({uname})\n"
        f"ID: {user.id}\n\n"
        f"{body}"
    )


@dp.message(F.chat.type == "private")
async def user_to_log(message: Message):
    user = message.from_user
    cid = _LOG_CHAT_ID
    if not cid:
        await message.answer("Поддержка временно недоступна. Попробуйте позже.")
        return

    forward_text = _get_forward_text(message)

    # Forward media if present
    if message.photo:
        sent = await bot.send_photo(cid, message.photo[-1].file_id, caption=forward_text)
    elif message.video:
        sent = await bot.send_video(cid, message.video.file_id, caption=forward_text)
    elif message.document:
        sent = await bot.send_document(cid, message.document.file_id, caption=forward_text)
    elif message.voice:
        sent = await bot.send_voice(cid, message.voice.file_id, caption=forward_text)
    elif message.video_note:
        sent = await bot.send_video_note(cid, message.video_note.file_id)
        await bot.send_message(cid, forward_text)
    elif message.sticker:
        sent = await bot.send_sticker(cid, message.sticker.file_id)
        await bot.send_message(cid, forward_text)
    elif message.text:
        sent = await bot.send_message(cid, forward_text)
    else:
        sent = await bot.send_message(cid, forward_text)

    _ticket_map[sent.message_id] = user.id
    await message.answer("✅ Сообщение отправлено в поддержку. Ответ придёт сюда.")


@dp.message(F.chat.id == int(_LOG_CHAT_ID), F.reply_to_message)
async def log_reply_to_user(message: Message):
    if not message.reply_to_message:
        return
    replied_id = message.reply_to_message.message_id
    user_id = _ticket_map.get(replied_id)
    if not user_id:
        await message.reply("❌ Не удалось определить пользователя (тикет не найден в памяти).")
        return
    text = message.text or message.caption or ""
    if not text:
        await message.reply("❌ Пустой ответ. Напишите текст.")
        return
    try:
        if message.photo:
            await bot.send_photo(user_id, message.photo[-1].file_id, caption=f"💬 Ответ поддержки:\n\n{text}")
        elif message.video:
            await bot.send_video(user_id, message.video.file_id, caption=f"💬 Ответ поддержки:\n\n{text}")
        elif message.document:
            await bot.send_document(user_id, message.document.file_id, caption=f"💬 Ответ поддержки:\n\n{text}")
        else:
            await bot.send_message(user_id, f"💬 Ответ поддержки:\n\n{text}")
        await message.reply("✅ Ответ отправлен пользователю.")
    except Exception as e:
        await message.reply(f"❌ Ошибка отправки пользователю: {e}")


async def run_support_bot():
    me = await bot.get_me()
    logging.info("Support bot started: @%s", me.username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
