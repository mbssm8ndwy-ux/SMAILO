from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config

SUPPORT_BOT_TOKEN = "8878588955:AAEFxn-Mm2-Qc8HnKnyVzBgQKmIR3WkAYvw"

session = AiohttpSession(proxy=config.PROXY_URL) if getattr(config, "PROXY_URL", "").strip() else None
bot = Bot(token=SUPPORT_BOT_TOKEN, session=session) if session else Bot(token=SUPPORT_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

LOG_CHAT_ID = int(config.LOG_CHAT_ID)

# log_message_id -> {"user_id": int, "category": str, "status": str}
_ticket_map: dict[int, dict] = {}
# user_id -> log_message_id (latest open ticket)
_user_ticket: dict[int, int] = {}


class TicketStates(StatesGroup):
    choosing_category = State()


CATEGORIES = {
    "ticket:q": "❓ Вопрос",
    "ticket:order": "📦 По заказу",
    "ticket:work": "💼 Работа",
    "ticket:other": "⚠️ Другое",
}
CATEGORY_NAMES = {v: k for k, v in CATEGORIES.items()}


def _categories_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for cb, label in CATEGORIES.items():
        kb.button(text=label, callback_data=cb)
    kb.adjust(1)
    return kb.as_markup()


def _close_ticket_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Закрыть тикет", callback_data=f"closeticket:{msg_id}")
    kb.adjust(1)
    return kb.as_markup()


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.set_state(TicketStates.choosing_category)
    await message.answer(
        "📩 Выберите категорию тикета:",
        reply_markup=_categories_keyboard(),
    )


@dp.message(Command("cancel"), StateFilter("*"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")


@dp.callback_query(F.data.startswith("ticket:"))
async def category_chosen(call: CallbackQuery, state: FSMContext):
    label = CATEGORIES.get(call.data)
    if not label:
        await call.answer()
        return
    await state.update_data(ticket_category=label)
    await state.clear()
    await call.message.edit_text(
        f"📩 Категория: {label}\n\n"
        "Опишите ваше обращение одним сообщением.\n/cancel — отмена."
    )
    await call.answer()


@dp.message(F.chat.type == "private", ~F.text.startswith("/"))
async def user_message_handler(message: Message, state: FSMContext):
    user = message.from_user
    cid = LOG_CHAT_ID
    if not cid:
        await message.answer("Поддержка временно недоступна.")
        return

    # If user already has an open ticket, forward to same thread
    existing_msg_id = _user_ticket.get(user.id)
    if existing_msg_id and existing_msg_id in _ticket_map:
        ticket = _ticket_map[existing_msg_id]
        if ticket["status"] == "open":
            # Forward to log chat as follow-up
            uname = f"@{user.username}" if user.username else "без username"
            body = message.text or message.caption or "(без текста)"
            text = (
                f"📎 Продолжение тикета\n"
                f"От: {user.full_name} ({uname})\n"
                f"ID: {user.id}\n\n"
                f"{body}"
            )
            sent = await bot.send_message(cid, text)
            _ticket_map[sent.message_id] = {"user_id": user.id, "category": ticket["category"], "status": "open"}
            _user_ticket[user.id] = sent.message_id
            await message.answer("✅ Сообщение добавлено в ваш тикет.")
            return

    # No open ticket — need category first
    st = await state.get_state()
    category = None
    if st:
        data = await state.get_data()
        category = data.get("ticket_category")
    if not category:
        await state.set_state(TicketStates.choosing_category)
        await message.answer(
            "📩 Сначала выберите категорию тикета:",
            reply_markup=_categories_keyboard(),
        )
        return

    # Create new ticket
    uname = f"@{user.username}" if user.username else "без username"
    body = message.text or message.caption or "(без текста)"
    text = (
        f"🆘 Новый тикет ({category})\n"
        f"От: {user.full_name} ({uname})\n"
        f"ID: {user.id}\n\n"
        f"{body}\n\n"
        "↩️ Ответьте реплаем — ответ уйдёт пользователю."
    )

    if message.photo:
        sent = await bot.send_photo(cid, message.photo[-1].file_id, caption=text, reply_markup=_close_ticket_keyboard(0))
        sent_msg_id = sent.message_id
        # Edit to add real message_id in callback
        await bot.edit_message_reply_markup(cid, sent_msg_id, reply_markup=_close_ticket_keyboard(sent_msg_id))
    elif message.video:
        sent = await bot.send_video(cid, message.video.file_id, caption=text, reply_markup=_close_ticket_keyboard(0))
        sent_msg_id = sent.message_id
        await bot.edit_message_reply_markup(cid, sent_msg_id, reply_markup=_close_ticket_keyboard(sent_msg_id))
    elif message.document:
        sent = await bot.send_document(cid, message.document.file_id, caption=text, reply_markup=_close_ticket_keyboard(0))
        sent_msg_id = sent.message_id
        await bot.edit_message_reply_markup(cid, sent_msg_id, reply_markup=_close_ticket_keyboard(sent_msg_id))
    elif message.voice:
        sent = await bot.send_voice(cid, message.voice.file_id, caption=text, reply_markup=_close_ticket_keyboard(0))
        sent_msg_id = sent.message_id
        await bot.edit_message_reply_markup(cid, sent_msg_id, reply_markup=_close_ticket_keyboard(sent_msg_id))
    elif message.video_note:
        sent = await bot.send_video_note(cid, message.video_note.file_id)
        sent2 = await bot.send_message(cid, text, reply_markup=_close_ticket_keyboard(0))
        sent_msg_id = sent2.message_id
        await bot.edit_message_reply_markup(cid, sent_msg_id, reply_markup=_close_ticket_keyboard(sent_msg_id))
        _ticket_map[sent_msg_id] = {"user_id": user.id, "category": category, "status": "open"}
    elif message.sticker:
        sent = await bot.send_sticker(cid, message.sticker.file_id)
        sent2 = await bot.send_message(cid, text, reply_markup=_close_ticket_keyboard(0))
        sent_msg_id = sent2.message_id
        await bot.edit_message_reply_markup(cid, sent_msg_id, reply_markup=_close_ticket_keyboard(sent_msg_id))
    else:
        sent = await bot.send_message(cid, text, reply_markup=_close_ticket_keyboard(0))
        sent_msg_id = sent.message_id
        await bot.edit_message_reply_markup(cid, sent_msg_id, reply_markup=_close_ticket_keyboard(sent_msg_id))

    _ticket_map[sent_msg_id] = {"user_id": user.id, "category": category, "status": "open"}
    _user_ticket[user.id] = sent_msg_id
    await state.clear()
    await message.answer("✅ Тикет создан. Ответ придёт сюда.")


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
    # Remove from user_ticket if this was the latest
    if _user_ticket.get(user_id) == msg_id:
        del _user_ticket[user_id]
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
    replied_id = message.reply_to_message.message_id
    ticket = _ticket_map.get(replied_id)
    if not ticket:
        await message.reply("❌ Пользователь не найден (тикет сброшен при перезапуске).")
        return
    if ticket["status"] == "closed":
        await message.reply("❌ Тикет уже закрыт. Пользователь написал заново — создаст новый.")
        return
    user_id = ticket["user_id"]
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
