from __future__ import annotations

import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from db import Database

SUPPORT_BOT_TOKEN = "8878588955:AAEFxn-Mm2-Qc8HnKnyVzBgQKmIR3WkAYvw"

session = AiohttpSession(proxy=config.PROXY_URL) if getattr(config, "PROXY_URL", "").strip() else None
bot = Bot(token=SUPPORT_BOT_TOKEN, session=session) if session else Bot(token=SUPPORT_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

LOG_CHAT_ID = int(config.LOG_CHAT_ID)
db = Database()

# log_msg_id -> {"user_id", "category", "status"}
_ticket_map: dict[int, dict] = {}
# user_id -> latest log_msg_id with open ticket
_user_ticket: dict[int, int] = {}
# user_id -> pending category (before first message sent)
_pending_category: dict[int, str] = {}
# user_id -> last category used (for auto-reopen after close)
_last_category: dict[int, str] = {}

CATEGORIES = {
    "ticket:q": "❓ Вопрос",
    "ticket:order": "📦 По заказу",
    "ticket:work": "💼 Работа",
    "ticket:other": "⚠️ Другое",
}


def _categories_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for cb, label in CATEGORIES.items():
        kb.button(text=label, callback_data=cb)
    kb.button(text="📋 Мои тикеты", callback_data="mytickets")
    kb.adjust(1)
    return kb.as_markup()


def _close_ticket_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Закрыть тикет", callback_data=f"closeticket:{msg_id}")
    kb.adjust(1)
    return kb.as_markup()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    _pending_category.pop(message.from_user.id, None)
    await message.answer(
        "📩 Выберите категорию тикета:",
        reply_markup=_categories_keyboard(),
    )


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message):
    _pending_category.pop(message.from_user.id, None)
    await message.answer("Отменено.")


@dp.message(Command("ping"))
async def cmd_ping(message: Message):
    await message.answer("pong")


@dp.callback_query(F.data.startswith("closeticket:"))
async def close_ticket(call: CallbackQuery):
    msg_id = int(call.data.split(":")[1])
    ticket = _ticket_map.get(msg_id)
    if not ticket:
        await call.answer("Тикет не найден", show_alert=True)
        return
    if ticket["status"] == "closed":
        await call.answer("Тикет уже закрыт")
        return
    ticket["status"] = "closed"
    user_id = ticket["user_id"]
    _last_category[user_id] = ticket["category"]
    if _user_ticket.get(user_id) == msg_id:
        del _user_ticket[user_id]
    db_id = ticket.get("db_id")
    if db_id:
        db.close_support_bot_ticket(db_id)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.edit_text(
        call.message.text + "\n\n✅ Тикет закрыт."
    )
    await call.answer("Тикет закрыт")
    try:
        await bot.send_message(user_id, "✅ Ваш тикет закрыт. Если нужна помощь — напишите ещё раз.")
    except Exception:
        pass


@dp.message(F.chat.id == LOG_CHAT_ID, F.reply_to_message)
async def log_reply_to_user(message: Message):
    replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    m = re.search(r"(?:ID|UID):\s*(\d+)", replied_text)
    if not m:
        await message.reply("❌ Не найден ID пользователя в сообщении.")
        return

    user_id = int(m.group(1))
    text = message.text or message.caption or ""
    if not text and not message.photo and not message.video:
        await message.reply("❌ Пустой ответ.")
        return

    try:
        if message.photo:
            await bot.send_photo(user_id, message.photo[-1].file_id, caption=f"💬 Ответ поддержки:\n\n{text}" if text else "💬 Ответ поддержки")
        elif message.video:
            await bot.send_video(user_id, message.video.file_id, caption=f"💬 Ответ поддержки:\n\n{text}" if text else "💬 Ответ поддержки")
        else:
            await bot.send_message(user_id, f"💬 Ответ поддержки:\n\n{text}")
        await message.reply("✅ Ответ отправлен.")
    except Exception as e:
        logging.exception("support_bot: failed to send reply to user %s", user_id)
        await message.reply(f"❌ Ошибка отправки: {e}")


async def run_support_bot():
    me = await bot.get_me()
    logging.info("Support bot started: @%s", me.username)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
