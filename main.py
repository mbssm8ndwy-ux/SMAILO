from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from aiohttp import web
import ssl
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.enums import ContentType
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from db import MAX_PAYMENT_CARDS, MAX_PAYMENT_SBP, Database
from scripts import keyboards
import support_bot

BOT_START_TIME = datetime.now()
_BOT_USERNAME: str | None = None
LOCAL_PORT = int(os.environ.get("PORT", 12337))


logging.basicConfig(level=logging.INFO)

session = AiohttpSession(proxy=config.PROXY_URL) if getattr(config, "PROXY_URL", "").strip() else None
bot = Bot(token=config.API_TOKEN, session=session) if session else Bot(token=config.API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db = Database()

_ADMIN_QTY_RE = re.compile(
    r"^(\d+(?:[.,]\d+)?)\s*(шт|г|g)$",
    re.IGNORECASE,
)


def _parse_admin_qty_spec(spec: str) -> tuple[float, str]:
    s = spec.strip().replace(" ", "")
    if not s:
        return 1.0, "шт"
    m = _ADMIN_QTY_RE.match(s)
    if not m:
        return 1.0, "шт"
    v = float(m.group(1).replace(",", "."))
    if v <= 0:
        return 1.0, "шт"
    u = m.group(2).lower()
    return v, ("г" if u in ("г", "g") else "шт")


def _looks_like_http_url(s: str) -> bool:
    x = s.strip().lower()
    return x.startswith("http://") or x.startswith("https://")


def _format_qty_line(product: dict) -> str:
    try:
        v = float(product.get("qty_value") if product.get("qty_value") is not None else 1)
    except (TypeError, ValueError):
        v = 1.0
    raw_u = (product.get("qty_unit") or "шт").strip().lower()
    sym = "г" if raw_u in ("г", "g") else "шт"
    return f"{v:g} {sym}"


DEFAULT_WELCOME_TEXT = (
    "Привет, {name}!\n\n"
    "Каталог: сначала ассортимент (группа), затем город, район и позиция с ценой. "
    "«Вернуться назад» возвращает на шаг назад."
)


_SUPPORT_BOT_USERNAME: str | None = None


def shop_support_contact() -> str:
    return _SUPPORT_BOT_USERNAME or _BOT_USERNAME or "@qcryptopay"


def shop_rules_text() -> str:
    return db.get_setting("rules_text") or config.RULES_TEXT


def shop_about_text() -> str:
    return db.get_setting("about_text") or config.ABOUT_TEXT


def shop_payment_support() -> str:
    return _SUPPORT_BOT_USERNAME or _BOT_USERNAME or "@qcryptopay"


def shop_reviews_text() -> str:
    return db.get_setting("reviews_text") or config.REVIEWS_TEXT


def shop_crypto_usdt_trc20() -> str:
    return (
        (db.get_setting("crypto_usdt_trc20") or "").strip()
        or (getattr(config, "CRYPTO_USDT_TRC20_ADDRESS", "") or "").strip()
    )


def shop_crypto_btc() -> str:
    return (
        (db.get_setting("crypto_btc") or "").strip()
        or (getattr(config, "CRYPTO_BTC_ADDRESS", "") or "").strip()
    )


def _topup_method_flags() -> dict:
    return {
        "card": db.count_payment_cards() > 0,
        "sbp": db.count_payment_sbp() > 0,
        "usdt": bool(shop_crypto_usdt_trc20()),
        "btc": bool(shop_crypto_btc()),
    }


def _any_topup_method_available() -> bool:
    f = _topup_method_flags()
    return any(f.values())


def _http_json(
    url: str, timeout_s: float = 4.0, *, insecure_ssl: bool = False
) -> dict | list | None:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; SkittlesMarketBot/1.0)",
                "Accept": "application/json,text/plain,*/*",
            },
        )
        ctx = ssl._create_unverified_context() if insecure_ssl else None
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        return json.loads(raw)
    except Exception:
        return None


def _garantex_rub_rate(kind: str) -> float | None:
    market_candidates = (
        ("usdtrub", "usdt_rub", "usdt-rub")
        if kind == "usdt"
        else ("btcrub", "btc_rub", "btc-rub")
    )
    for m in market_candidates:
        url = "https://garantex.org/api/v2/depth?" + urllib.parse.urlencode(
            {"market": m}
        )
        data = _http_json(url, insecure_ssl=True)
        if not isinstance(data, dict):
            continue
        asks = data.get("asks")
        bids = data.get("bids")
        prices: list[float] = []
        if isinstance(asks, list) and asks:
            try:
                prices.append(float(asks[0]["price"]))
            except Exception:
                pass
        if isinstance(bids, list) and bids:
            try:
                prices.append(float(bids[0]["price"]))
            except Exception:
                pass
        if prices:
            return sum(prices) / len(prices)
    return None


def _bybit_rub_rate(kind: str) -> float | None:
    symbol = "USDTRUB" if kind == "usdt" else "BTCRUB"
    hosts = ("https://api.bybit.com", "https://api.bytick.com")
    for host in hosts:
        url = host + "/v5/market/tickers?" + urllib.parse.urlencode(
            {"category": "spot", "symbol": symbol}
        )
        data = _http_json(url)
        if not isinstance(data, dict):
            continue
        result = data.get("result")
        if not isinstance(result, dict):
            continue
        rows = result.get("list")
        if not isinstance(rows, list) or not rows:
            continue
        try:
            p = float(rows[0]["lastPrice"])
            if p > 0:
                return p
        except Exception:
            continue
    return None


def _coingecko_rub_rate(kind: str) -> float | None:
    ids = "tether,bitcoin"
    url = (
        "https://api.coingecko.com/api/v3/simple/price?"
        + urllib.parse.urlencode({"ids": ids, "vs_currencies": "rub"})
    )
    data = _http_json(url)
    if not isinstance(data, dict):
        return None
    key = "tether" if kind == "usdt" else "bitcoin"
    node = data.get(key)
    if not isinstance(node, dict):
        return None
    try:
        p = float(node.get("rub"))
        return p if p > 0 else None
    except Exception:
        return None


def _crypto_amount_by_rub(amount_rub: float, kind: str) -> tuple[float, str] | None:
    """
    Возвращает (сколько монет оплатить, источник курса).
    kind: usdt | btc
    """
    if amount_rub <= 0:
        return None
    rate = _garantex_rub_rate(kind)
    if rate and rate > 0:
        return amount_rub / rate, "Garantex"
    rate = _bybit_rub_rate(kind)
    if rate and rate > 0:
        return amount_rub / rate, "Bybit"
    rate = _coingecko_rub_rate(kind)
    if rate and rate > 0:
        return amount_rub / rate, "CoinGecko"
    return None


def _purchase_requisite_for_method(kind: str, amount: float, user_id: int) -> Optional[str]:
    if kind == "usdt":
        addr = shop_crypto_usdt_trc20()
        if not addr:
            return None
        quoted = _crypto_amount_by_rub(amount, "usdt")
        crypto_line = (
            f"💱 По курсу: к оплате примерно {quoted[0]:.2f} USDT (источник: {quoted[1]}).\n"
            if quoted
            else "💱 Не удалось получить курс (Garantex/Bybit/CoinGecko). Уточните сумму в поддержке.\n"
        )
        return (
            "💚 USDT · сеть TRC20 (Tron)\n"
            f"Адрес кошелька:\n{addr}\n\n"
            f"💵 К оплате за заказ: {amount:.2f} ₽\n"
            f"{crypto_line}\n"
            "⚠️ Не используйте другие сети (ERC20, BEP20 и т.д.).\n\n"
            f"📝 В memo / комментарии к переводу укажите Telegram ID: {user_id}"
        )
    if kind == "btc":
        addr = shop_crypto_btc()
        if not addr:
            return None
        quoted = _crypto_amount_by_rub(amount, "btc")
        crypto_line = (
            f"💱 По курсу: к оплате примерно {quoted[0]:.8f} BTC (источник: {quoted[1]}).\n"
            if quoted
            else "💱 Не удалось получить курс (Garantex/Bybit/CoinGecko). Уточните сумму в поддержке.\n"
        )
        return (
            "🟠 Bitcoin (BTC)\n"
            f"Адрес:\n{addr}\n\n"
            f"💵 К оплате за заказ: {amount:.2f} ₽\n"
            f"{crypto_line}\n"
            f"📝 Если биржа позволяет комментарий к выводу — укажите Telegram ID: {user_id}"
        )
    return None


def _topup_requisite_for_method(kind: str, amount: float, user_id: int) -> Optional[str]:
    amt_s = f"{amount:.0f}"
    if kind == "card":
        card = db.pick_next_payment_card()
        if not card:
            return None
        return (
            "💳 Банковская карта\n"
            f"{card['details']}\n\n"
            f"💵 К зачислению на баланс: {amt_s} ₽"
        )
    if kind == "sbp":
        sbp = db.pick_next_payment_sbp()
        if not sbp:
            return None
        return (
            "💠 СБП (Система быстрых платежей)\n"
            f"{sbp['details']}\n\n"
            f"💵 Сумма к переводу: {amt_s} ₽"
        )
    if kind == "usdt":
        addr = shop_crypto_usdt_trc20()
        if not addr:
            return None
        quoted = _crypto_amount_by_rub(amount, "usdt")
        crypto_line = (
            f"💱 По курсу: к оплате примерно {quoted[0]:.2f} USDT (источник: {quoted[1]}).\n"
            if quoted
            else "💱 Не удалось получить курс (Garantex/Bybit). Уточните сумму в поддержке.\n"
        )
        return (
            "💚 USDT · сеть TRC20 (Tron)\n"
            f"Адрес кошелька:\n{addr}\n\n"
            f"💵 Зачислим на баланс: {amt_s} ₽\n"
            f"{crypto_line}\n"
            "⚠️ Не используйте другие сети (ERC20, BEP20 и т.д.) — средства можно потерять.\n\n"
            f"📝 В memo / комментарии к переводу укажите ваш Telegram ID: {user_id}"
        )
    if kind == "btc":
        addr = shop_crypto_btc()
        if not addr:
            return None
        quoted = _crypto_amount_by_rub(amount, "btc")
        crypto_line = (
            f"💱 По курсу: к оплате примерно {quoted[0]:.8f} BTC (источник: {quoted[1]}).\n"
            if quoted
            else "💱 Не удалось получить курс (Garantex/Bybit). Уточните сумму в поддержке.\n"
        )
        return (
            "🟠 Bitcoin (BTC)\n"
            f"Адрес:\n{addr}\n\n"
            f"💵 К зачислению на баланс: {amt_s} ₽\n"
            f"{crypto_line}\n"
            f"📝 Если биржа позволяет комментарий к выводу — укажите Telegram ID: {user_id}"
        )
    return None


def _topup_method_label(requisite_text: str) -> str:
    head = requisite_text[:120]
    if "USDT" in head and "TRC20" in head:
        return "💚 USDT TRC20"
    if "Bitcoin" in head or "BTC" in requisite_text[:40]:
        return "🟠 BTC"
    if "💠" in head or "СБП" in head:
        return "💠 СБП"
    if "💳" in head or "Карта" in head:
        return "💳 Карта"
    return "Способ"


def _telegram_plain_chunks(text: str, limit: int = 4000) -> list[str]:
    """Несколько сообщений под лимит Telegram (~4096 символов на сообщение)."""
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


async def _send_admin_plain_chunks(message: Message, text: str) -> None:
    chunks = _telegram_plain_chunks(text)
    n = len(chunks)
    for idx, ch in enumerate(chunks):
        prefix = f"📄 Часть {idx + 1}/{n}\n" if n > 1 else ""
        await message.answer(prefix + ch)


def welcome_template() -> str:
    return db.get_setting("welcome_text") or DEFAULT_WELCOME_TEXT


def welcome_text_for_user(full_name: str) -> str:
    return welcome_template().replace("{name}", full_name)


async def send_welcome(message: Message) -> None:
    text = welcome_text_for_user(message.from_user.full_name)
    photo_id = db.get_setting("welcome_photo_file_id")
    kb = keyboards.main_menu(is_admin(message.from_user.id))
    if photo_id:
        cap = text if len(text) <= 1024 else text[:1021] + "..."
        await message.answer_photo(photo_id, caption=cap, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


def parse_admin_ids() -> set:
    result = set()
    for value in config.ADMIN_IDS.split(","):
        value = value.strip()
        if value.isdigit():
            result.add(int(value))
    return result


ADMIN_IDS = parse_admin_ids()


class UserStates(StatesGroup):
    captcha_wait = State()
    support_wait_text = State()
    order_review_rating = State()
    order_review_text = State()
    promo_enter = State()
    work_city = State()


class AdminStates(StatesGroup):
    add_city_name = State()
    add_district_payload = State()
    add_product_payload = State()
    add_product_wizard_assortment_input = State()
    add_product_wizard_title_input = State()
    add_product_wizard_qty_input = State()
    add_product_from_last_payload = State()
    update_price_payload = State()
    delete_payload = State()
    add_pay_card = State()
    add_pay_sbp = State()
    order_delivery_input = State()
    order_delivery_confirm = State()
    set_welcome_text = State()
    set_welcome_photo = State()
    set_about_text = State()
    set_rules_text = State()
    set_support_contact = State()
    set_pay_support_contact = State()
    set_reviews_body = State()
    admin_bal_credit = State()
    admin_bal_debit = State()
    admin_bal_lookup = State()
    set_crypto_usdt = State()
    set_crypto_btc = State()
    add_assortment_name = State()
    broadcast_compose = State()
    broadcast_confirm = State()
    promo_add_name = State()
    promo_add_percent = State()
    promo_add_uses = State()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _is_log_chat_message(message: Message) -> bool:
    if not getattr(config, "LOG_CHAT_ID", None):
        return False
    return message.chat.id == int(config.LOG_CHAT_ID)


_promo_cache: dict[int, dict] = {}
_work_cache: dict[int, dict] = {}


_BROADCAST_SKIP_CONTENT = frozenset(
    {
        ContentType.NEW_CHAT_MEMBERS,
        ContentType.LEFT_CHAT_MEMBER,
        ContentType.GROUP_CHAT_CREATED,
        ContentType.SUPERGROUP_CHAT_CREATED,
        ContentType.CHANNEL_CHAT_CREATED,
        ContentType.MESSAGE_AUTO_DELETE_TIMER_CHANGED,
        ContentType.VIDEO_CHAT_STARTED,
        ContentType.VIDEO_CHAT_ENDED,
        ContentType.VIDEO_CHAT_PARTICIPANTS_INVITED,
        ContentType.GENERAL_FORUM_TOPIC_HIDDEN,
        ContentType.GENERAL_FORUM_TOPIC_UNHIDDEN,
        ContentType.WRITE_ACCESS_ALLOWED,
        ContentType.FORUM_TOPIC_CREATED,
        ContentType.FORUM_TOPIC_CLOSED,
        ContentType.FORUM_TOPIC_REOPENED,
        ContentType.FORUM_TOPIC_EDITED,
        ContentType.GIVEAWAY_CREATED,
        ContentType.GIVEAWAY,
        ContentType.GIVEAWAY_WINNERS,
        ContentType.PROXIMITY_ALERT_TRIGGERED,
        ContentType.USER_SHARED,
        ContentType.CHAT_SHARED,
    }
)


async def _run_broadcast_task(
    admin_id: int, from_chat_id: int, message_id: int
) -> None:
    user_ids = db.list_bot_user_ids()
    ok = 0
    blocked = 0
    other_fail = 0
    for uid in user_ids:
        try:
            await bot.copy_message(
                chat_id=uid,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            ok += 1
        except TelegramForbiddenError:
            blocked += 1
        except Exception:
            logging.exception("Рассылка: не удалось отправить user_id=%s", uid)
            other_fail += 1
        await asyncio.sleep(0.055)
    summary = (
        "📣 Рассылка завершена.\n"
        f"Успешно: {ok}\n"
        f"Заблокировали бота / недоступно: {blocked}\n"
        f"Другие ошибки: {other_fail}"
    )
    try:
        await bot.send_message(admin_id, summary)
    except Exception:
        logging.exception("Не удалось прислать отчёт о рассылке админу %s", admin_id)


def _forum_topic_id() -> int | None:
    raw = getattr(config, "LOG_FORUM_TOPIC_ID", None)
    if raw is None or raw == "":
        return None
    return int(raw)


def _payment_notify_chat_id() -> int | None:
    """Чат для карточек заказов и текстовой выдачи (логи оплат)."""
    for attr in ("PAYMENT_NOTIFY_CHAT_ID", "LOG_CHAT_ID"):
        raw = getattr(config, attr, None)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


def _payment_notify_topic_id() -> int | None:
    raw = getattr(config, "PAYMENT_NOTIFY_TOPIC_ID", None)
    if raw is not None and str(raw).strip() != "":
        try:
            t = int(raw)
            return t if t > 0 else None
        except (TypeError, ValueError):
            pass
    return _forum_topic_id()


def _pay_method_label(pay_method: str | None) -> str:
    m = (pay_method or "").strip().lower()
    if m == "balance":
        return "Баланс"
    if m == "sbp":
        return "СБП"
    if m == "card":
        return "Карта"
    return m or "—"


async def send_event_log(user: types.User, event_text: str) -> None:
    if not getattr(config, "LOG_CHAT_ID", None):
        return
    username = f"@{user.username}" if user.username else "без username"
    text = (
        "Отстук события:\n"
        f"Пользователь: {user.full_name} ({username})\n"
        f"ID: {user.id}\n"
        f"Событие: {event_text}"
    )
    tid = _forum_topic_id()
    try:
        kwargs: dict = {"chat_id": config.LOG_CHAT_ID, "text": text}
        if tid is not None:
            kwargs["message_thread_id"] = tid
        await bot.send_message(**kwargs)
    except Exception:
        logging.exception("Failed to send event log")


async def send_user_start_log(message: Message) -> None:
    if not getattr(config, "LOG_CHAT_ID", None):
        return
    user = message.from_user
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = (
        "👤 НОВЫЙ ПОЛЬЗОВАТЕЛЬ /start\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Время: {now}\n"
        f"📛 Имя: {user.full_name}\n"
        f"👤 Username: @{user.username if user.username else 'нет'}\n"
        f"🆔 TG ID: {user.id}\n"
        f"🌐 Language: {user.language_code if user.language_code else 'не указан'}\n"
        f"🤖 Бот: {'Да' if user.is_bot else 'Нет'}\n"
        f"💬 Чат ID: {message.chat.id}\n"
        f"📱 Тип чата: {message.chat.type}\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    tid = _forum_topic_id()
    try:
        kwargs: dict = {"chat_id": config.LOG_CHAT_ID, "text": text}
        if tid is not None:
            kwargs["message_thread_id"] = tid
        await bot.send_message(**kwargs)
    except Exception:
        logging.exception("Failed to send user start log")


async def send_referral_registered_log(invited: types.User, referrer_id: int) -> None:
    """В LOG_CHAT — кто пришёл по реф-ссылке и чей это реферал."""
    if not getattr(config, "LOG_CHAT_ID", None):
        return
    if invited.username:
        inv_line = f"{invited.full_name} (@{invited.username}), id {invited.id}"
    else:
        inv_line = f"{invited.full_name}, id {invited.id}"
    ref_line = f"только id {referrer_id} (профиль недоступен)"
    try:
        ref_chat = await bot.get_chat(referrer_id)
        ref_un = getattr(ref_chat, "username", None) or ""
        ref_fn = (
            getattr(ref_chat, "full_name", None)
            or getattr(ref_chat, "title", None)
            or ""
        ).strip()
        bits: list[str] = []
        if ref_fn:
            bits.append(ref_fn)
        if ref_un:
            bits.append(f"@{ref_un}")
        if bits:
            ref_line = " ".join(bits) + f", id {referrer_id}"
        else:
            ref_line = f"id {referrer_id}"
    except Exception:
        logging.debug("get_chat referrer_id=%s failed", referrer_id, exc_info=True)
    text = (
        "🌶️ Реферальная система: новый пользователь по ссылке\n\n"
        f"Приглашённый: {inv_line}\n"
        f"Пригласил: {ref_line}"
    )
    tid = _forum_topic_id()
    try:
        kwargs: dict = {"chat_id": config.LOG_CHAT_ID, "text": text}
        if tid is not None:
            kwargs["message_thread_id"] = tid
        await bot.send_message(**kwargs)
    except Exception:
        logging.exception("Failed to send referral log")


async def post_review_to_log_chat(
    order_id: int,
    buyer: types.User,
    review_text: str,
    product_line: str,
) -> None:
    if not getattr(config, "LOG_CHAT_ID", None):
        return
    uname = f"@{buyer.username}" if buyer.username else f"id {buyer.id}"
    header = (
        f"⭐ Отзыв по заказу #{order_id}\n"
        f"Покупатель: {buyer.full_name} ({uname})\n"
        f"Позиция: {product_line}\n\n"
    )
    room = max(500, 4096 - len(header) - 16)
    tail = (
        review_text if len(review_text) <= room else review_text[: room - 3] + "..."
    )
    text = header + tail
    tid = _forum_topic_id()
    try:
        kwargs: dict = {"chat_id": config.LOG_CHAT_ID, "text": text}
        if tid is not None:
            kwargs["message_thread_id"] = tid
        await bot.send_message(**kwargs)
    except Exception:
        logging.exception("Failed to send order review to log chat")


def _product_label(product: dict) -> str:
    """Подпись позиции для логов и сообщений (ассортимент · позиция · количество)."""
    a = (product.get("assortment_name") or "").strip()
    t = (product.get("title") or "").strip()
    q = _format_qty_line(product)
    if a and t:
        return f"{a} · {t} ({q})"
    if t:
        return f"{t} ({q})"
    if a:
        return f"{a} ({q})"
    return "?"


def _catalog_back_for_product(product_id: int) -> str:
    product = db.get_product(product_id)
    if not product:
        return "nav:x"
    district = db.get_district(product["district_id"])
    if not district:
        return "nav:x"
    names = db.get_assortment_names()
    try:
        name_idx = names.index(product["assortment_name"])
    except ValueError:
        return "nav:x"
    city_id = district["city_id"]
    page = name_idx // keyboards.CATALOG_NAMES_PER_PAGE
    return f"nav:b3:{name_idx}:{city_id}:{district['id']}:{page}"


def _product_card_content(
    product_id: int,
    *,
    back_callback_data: Optional[str] = None,
) -> Optional[Tuple[str, InlineKeyboardMarkup]]:
    product = db.get_product(product_id)
    if not product:
        return None
    district = db.get_district(product["district_id"])
    city = db.get_city(district["city_id"]) if district else None
    auto_note = ""
    if product.get("auto_delivery_url"):
        auto_note = "\n⚡ После оплаты ссылка придёт автоматически."
    text = (
        f"📂 Ассортимент: {product['assortment_name']}\n"
        f"📌 Позиция: {product['title']}\n"
        f"📏 Количество: {_format_qty_line(product)}\n"
        f"Город: {city['name'] if city else '-'}\n"
        f"Район: {district['name'] if district else '-'}\n"
        f"💰 Цена: {product['price']:.2f} RUB{auto_note}\n\n"
        "Нажмите кнопку ниже, чтобы перейти к оплате."
    )
    back_cd = back_callback_data or _catalog_back_for_product(product_id)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Перейти к оплате", callback_data=f"pay:{product['id']}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=keyboards.BTN_BACK,
                    callback_data=back_cd,
                )
            ],
        ]
    )
    return text, markup


async def post_pending_order_card_to_log_chat(
    order_id: int,
    buyer: types.User,
    product_name: str,
    order: dict,
) -> None:
    cid = _payment_notify_chat_id()
    if cid is None:
        return
    method_label = _pay_method_label(order.get("pay_method"))
    uname_line = f"@{buyer.username}" if buyer.username else f"id {buyer.id}"
    req = order.get("requisite_text") or "—"
    try:
        amt = float(order["amount"])
    except (TypeError, ValueError):
        amt = 0.0
    body = (
        f"💳 Заказ #{order_id} — подтверждение оплаты\n\n"
        f"Покупатель: {buyer.full_name} ({uname_line})\n"
        f"Позиция: {product_name}\n"
        f"Сумма: {amt:.2f} RUB\n"
        f"Способ: {method_label}\n"
        f"Реквизиты:\n{req}\n\n"
        "▸ «Выдать» → затем одним сообщением пришлите текст выдачи "
        "в этом чате (бот должен видеть сообщения админов в группе)."
    )
    if len(body) > 4096:
        body = body[:4093] + "..."
    tid = _payment_notify_topic_id()
    kw: dict = {
        "chat_id": cid,
        "text": body,
        "reply_markup": keyboards.admin_order_actions(order_id),
    }
    if tid is not None:
        kw["message_thread_id"] = tid
    try:
        await bot.send_message(**kw)
    except Exception:
        logging.exception("Не удалось отправить карточку заказа %s в лог-чат", order_id)


async def post_order_delivery_audit_to_log_chat(
    order_id: int, draft: str, buyer_label: str
) -> None:
    cid = _payment_notify_chat_id()
    if cid is None:
        return
    header = (
        f"✅ Заказ #{order_id} выдан покупателю\n"
        f"Покупатель: {buyer_label}\n\n"
        f"📬 Отправленный текст:\n"
    )
    room = max(500, 4096 - len(header) - 20)
    tail = draft if len(draft) <= room else draft[: room - 3] + "..."
    text = header + tail
    tid = _payment_notify_topic_id()
    kw: dict = {"chat_id": cid, "text": text}
    if tid is not None:
        kw["message_thread_id"] = tid
    try:
        await bot.send_message(**kw)
    except Exception:
        logging.exception("Не удалось записать выдачу заказа %s в лог-чат", order_id)


async def post_pending_topup_card_to_log_chat(
    request_id: int, buyer: types.User, req: dict
) -> None:
    cid = _payment_notify_chat_id()
    if cid is None:
        return
    method = _topup_method_label(req.get("requisite_text") or "")
    uname_line = f"@{buyer.username}" if buyer.username else f"id {buyer.id}"
    req_txt = (req.get("requisite_text") or "").strip() or "—"
    try:
        amt = float(req["amount"])
    except (TypeError, ValueError):
        amt = 0.0
    body = (
        f"💵 Пополнение баланса #{request_id} — «Я оплатил»\n\n"
        f"Пользователь: {buyer.full_name} ({uname_line})\n"
        f"Сумма: {amt:.0f} ₽\n"
        f"Способ: {method}\n"
        f"Реквизиты:\n{req_txt}\n\n"
        "Подтвердите зачисление или отклоните заявку кнопками ниже."
    )
    if len(body) > 4096:
        body = body[:4093] + "..."
    tid = _payment_notify_topic_id()
    kw: dict = {
        "chat_id": cid,
        "text": body,
        "reply_markup": keyboards.admin_topup_decide_markup(request_id),
    }
    if tid is not None:
        kw["message_thread_id"] = tid
    try:
        await bot.send_message(**kw)
    except Exception:
        logging.exception(
            "Не удалось отправить карточку пополнения %s в лог-чат", request_id
        )


async def notify_pending_order(order_id: int, buyer: types.User, product_name: str) -> None:
    order = db.get_order(order_id)
    if not order:
        return
    uname = f"@{buyer.username}" if buyer.username else "без username"
    await send_event_log(
        buyer,
        f"Заказ #{order_id}: «Я оплатил», позиция «{product_name}», сумма {order['amount']:.2f} RUB",
    )
    await post_pending_order_card_to_log_chat(order_id, buyer, product_name, order)

    pcid = _payment_notify_chat_id()
    if pcid is not None:
        short = (
            f"📌 Заказ #{order_id} ждёт подтверждения — карточка с кнопками "
            f"«Выдать/Отменить» в чате логов ({pcid})."
        )
    else:
        short = (
            f"💳 Заказ #{order_id} ждёт подтверждения.\n"
            "В config не задан LOG_CHAT_ID — карточка в группу не отправлена. "
            "⚙️ Админка → 📦 Подтверждения оплат"
        )
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, short)
        except Exception:
            logging.exception("Не удалось уведомить админа %s о заказе %s", aid, order_id)


async def _finalize_after_balance_payment(
    call: CallbackQuery, order_id: int, product: dict
) -> None:
    product_name = _product_label(product)
    amount = float(product["price"])
    uid = call.from_user.id
    auto_url = product.get("auto_delivery_url")
    if auto_url and str(auto_url).strip():
        url = str(auto_url).strip()
        if db.complete_order_from_awaiting(order_id, url):
            try:
                await call.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await call.message.answer(
                "Оплачено с баланса. Выдача:\n\n" + url,
                reply_markup=keyboards.order_review_invite_markup(order_id),
            )
            await call.answer()
            await send_event_log(
                call.from_user,
                f"Заказ #{order_id}: баланс, автовыдача «{product_name}»",
            )
            return
        db.refund_order_balance(uid, amount)
        await call.answer(
            "Ошибка выдачи. Средства возвращены на баланс.",
            show_alert=True,
        )
        return
    db.set_order_status(order_id, "pending_confirm")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        "Списано с баланса. Заявка на проверке — после подтверждения вы получите выдачу."
    )
    await call.answer()
    await notify_pending_order(order_id, call.from_user, product_name)


@dp.message(Command("start"), StateFilter("*"))
async def start(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    uid = message.from_user.id
    if not is_admin(uid) and not db.is_captcha_passed(uid):
        db.upsert_bot_user(uid, message.from_user.username)
        await state.set_state(UserStates.captcha_wait)
        await message.answer(
            "🤖 Подтвердите, что вы не бот.\nНажмите кнопку ниже:",
            reply_markup=keyboards.captcha_keyboard(),
        )
        return
    await send_user_start_log(message)
    if command.args:
        arg = command.args.strip()
        if arg.startswith("ref_"):
            tail = arg[4:]
            if tail.isdigit():
                rid = int(tail)
                if db.try_register_referral(message.from_user.id, rid):
                    await send_referral_registered_log(message.from_user, rid)
    db.upsert_bot_user(message.from_user.id, message.from_user.username)
    await send_welcome(message)
    await send_event_log(message.from_user, "Пользователь запустил бота")


@dp.message(Command("rules"), StateFilter("*"))
async def rules(message: Message):
    text = shop_rules_text()
    await message.answer(text, disable_web_page_preview=True)


@dp.callback_query(F.data == "captcha:verify", StateFilter(UserStates.captcha_wait))
async def captcha_verify(call: CallbackQuery, state: FSMContext):
    db.pass_captcha(call.from_user.id)
    await state.clear()
    await call.message.edit_text("✅ Проверка пройдена!")
    db.upsert_bot_user(call.from_user.id, call.from_user.username)
    await send_welcome(call.message)
    await call.answer()
    await send_user_start_log(call.message)
    await send_event_log(call.from_user, "Пользователь прошёл капчу")


@dp.message(Command("cancel"), StateFilter("*"))
async def cancel_handler(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного действия.")
        return
    await state.clear()
    await message.answer("Действие отменено.")


def _catalog_txt_assortment() -> str:
    return "Каталог:"


def _catalog_txt_city(assortment: str) -> str:
    return (
        "Выбор города\n\n"
        f"Ассортимент: {assortment}\n\n"
        "Выберите город:"
    )


def _catalog_txt_district(assortment: str, city_name: str) -> str:
    return (
        "Выбор района\n\n"
        f"Ассортимент: {assortment}\n"
        f"Город: {city_name}\n\n"
        "Выберите район:"
    )


def _catalog_txt_positions(assortment: str, city_name: str, district_name: str) -> str:
    return (
        "Выбор позиции\n\n"
        f"Ассортимент: {assortment}\n"
        f"Город: {city_name}\n"
        f"Район: {district_name}\n\n"
        "Выберите позицию:"
    )


def _catalog_names_or_none() -> Optional[list]:
    names = db.get_assortment_names()
    return names if names else None


async def _catalog_render_positions_list(
    call: CallbackQuery,
    name_idx: int,
    city_id: int,
    district_id: int,
    page: int,
) -> bool:
    names = db.get_assortment_names()
    if name_idx < 0 or name_idx >= len(names):
        await call.answer("Ошибка каталога", show_alert=True)
        return False
    name = names[name_idx]
    city = db.get_city(city_id)
    district = db.get_district(district_id)
    products = db.list_positions_by_district_assortment(district_id, name)
    if not city or not district or not products:
        await call.answer("Позиции недоступны", show_alert=True)
        return False
    await call.message.edit_text(
        _catalog_txt_positions(name, city["name"], district["name"]),
        reply_markup=keyboards.catalog_positions_keyboard(
            name_idx, city_id, district_id, page, products
        ),
    )
    return True


@dp.message(F.text.in_({"Каталог", "🛒 Каталог"}))
async def catalog_open(message: Message):
    names = db.get_assortment_names()
    if not names:
        await message.answer(
            "Каталог пока пуст: нет ни одной позиции с привязкой к ассортименту.\n"
            "В админке: district_id|Ассортимент|Название|Цена|[1шт или 2г]|ссылка"
        )
        return
    await message.answer(
        _catalog_txt_assortment(),
        reply_markup=keyboards.catalog_assortment_keyboard(names, 0),
    )


@dp.callback_query(F.data.startswith("nav:"))
async def catalog_navigation(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    if len(parts) < 2:
        await call.answer()
        return
    op = parts[1]

    if op == "work":
        await state.set_state(UserStates.work_city)
        await call.message.edit_text("🌆 Введите ваш город (например, Симферополь):")
        await call.answer()
        return

    if op == "z":
        await call.answer()
        return

    if op == "x":
        await call.message.edit_text(
            "Каталог закрыт. Откройте снова кнопкой «🛒 Каталог».",
            reply_markup=None,
        )
        await call.answer()
        return

    try:
        if op == "pg":
            page = int(parts[2])
            names = _catalog_names_or_none()
            if names is None:
                await call.answer("Каталог пуст", show_alert=True)
                return
            await call.message.edit_text(
                _catalog_txt_assortment(),
                reply_markup=keyboards.catalog_assortment_keyboard(names, page),
            )
            await call.answer()
            return

        if op == "s":
            name_idx = int(parts[2])
            names = _catalog_names_or_none()
            if names is None:
                await call.answer("Каталог пуст", show_alert=True)
                return
            if name_idx < 0 or name_idx >= len(names):
                await call.answer("Нет такого товара в ассортименте", show_alert=True)
                return
            assortment = names[name_idx]
            cities = db.get_cities_for_assortment_name(assortment)
            page = name_idx // keyboards.CATALOG_NAMES_PER_PAGE
            if not cities:
                await call.answer("Нет городов для этого ассортимента", show_alert=True)
                return
            await call.message.edit_text(
                _catalog_txt_city(assortment),
                reply_markup=keyboards.catalog_cities_keyboard(name_idx, page, cities),
            )
            await call.answer()
            await send_event_log(call.from_user, f"Каталог: ассортимент «{assortment}»")
            return

        if op == "g" and len(parts) >= 5:
            name_idx = int(parts[2])
            city_id = int(parts[3])
            page = int(parts[4])
            names = db.get_assortment_names()
            if name_idx < 0 or name_idx >= len(names):
                await call.answer("Ошибка каталога", show_alert=True)
                return
            assortment = names[name_idx]
            city = db.get_city(city_id)
            districts = db.get_districts_for_assortment_in_city(assortment, city_id)
            if not city or not districts:
                await call.answer("Районы недоступны", show_alert=True)
                return
            await call.message.edit_text(
                _catalog_txt_district(assortment, city["name"]),
                reply_markup=keyboards.catalog_districts_keyboard(
                    name_idx, city_id, page, districts
                ),
            )
            await call.answer()
            await send_event_log(
                call.from_user,
                f"Каталог: город «{city['name']}», ассортимент «{assortment}»",
            )
            return

        if op == "r" and len(parts) >= 5:
            name_idx = int(parts[2])
            city_id = int(parts[3])
            district_id = int(parts[4])
            page = name_idx // keyboards.CATALOG_NAMES_PER_PAGE
            if await _catalog_render_positions_list(
                call, name_idx, city_id, district_id, page
            ):
                names = db.get_assortment_names()
                assortment = names[name_idx]
                district = db.get_district(district_id)
                await send_event_log(
                    call.from_user,
                    f"Каталог: район «{district['name']}», ассортимент «{assortment}»",
                )
            await call.answer()
            return

        if op == "p" and len(parts) >= 3:
            product_id = int(parts[2])
            product = db.get_product(product_id)
            if not product:
                await call.answer("Позиция не найдена", show_alert=True)
                return
            district = db.get_district(product["district_id"])
            if not district:
                await call.answer("Ошибка каталога", show_alert=True)
                return
            names = db.get_assortment_names()
            try:
                name_idx = names.index(product["assortment_name"])
            except ValueError:
                await call.answer("Ошибка каталога", show_alert=True)
                return
            city_id = district["city_id"]
            district_id = product["district_id"]
            page = name_idx // keyboards.CATALOG_NAMES_PER_PAGE
            back_cd = f"nav:b3:{name_idx}:{city_id}:{district_id}:{page}"
            content = _product_card_content(product_id, back_callback_data=back_cd)
            if not content:
                await call.answer("Позиция не найдена", show_alert=True)
                return
            text, markup = content
            await call.message.edit_text(text, reply_markup=markup)
            await call.answer()
            await send_event_log(
                call.from_user,
                f"Каталог: позиция «{_product_label(product)}» ({product['price']:.2f} RUB)",
            )
            return

        if op == "b0" and len(parts) >= 3:
            page = int(parts[2])
            names = _catalog_names_or_none()
            if names is None:
                await call.answer("Каталог пуст", show_alert=True)
                return
            await call.message.edit_text(
                _catalog_txt_assortment(),
                reply_markup=keyboards.catalog_assortment_keyboard(names, page),
            )
            await call.answer()
            return

        if op == "b1" and len(parts) >= 4:
            name_idx = int(parts[2])
            page = int(parts[3])
            names = db.get_assortment_names()
            if name_idx < 0 or name_idx >= len(names):
                await call.answer("Ошибка", show_alert=True)
                return
            assortment = names[name_idx]
            cities = db.get_cities_for_assortment_name(assortment)
            if not cities:
                names_all = _catalog_names_or_none()
                if names_all is None:
                    await call.answer("Каталог пуст", show_alert=True)
                    return
                page = name_idx // keyboards.CATALOG_NAMES_PER_PAGE
                await call.message.edit_text(
                    _catalog_txt_assortment(),
                    reply_markup=keyboards.catalog_assortment_keyboard(names_all, page),
                )
                await call.answer()
                return
            await call.message.edit_text(
                _catalog_txt_city(assortment),
                reply_markup=keyboards.catalog_cities_keyboard(name_idx, page, cities),
            )
            await call.answer()
            return

        if op == "b2" and len(parts) >= 5:
            name_idx = int(parts[2])
            city_id = int(parts[3])
            page = int(parts[4])
            names = db.get_assortment_names()
            if name_idx < 0 or name_idx >= len(names):
                await call.answer("Ошибка", show_alert=True)
                return
            assortment = names[name_idx]
            city = db.get_city(city_id)
            districts = db.get_districts_for_assortment_in_city(assortment, city_id)
            if not city or not districts:
                await call.answer("Данные устарели", show_alert=True)
                return
            await call.message.edit_text(
                _catalog_txt_district(assortment, city["name"]),
                reply_markup=keyboards.catalog_districts_keyboard(
                    name_idx, city_id, page, districts
                ),
            )
            await call.answer()
            return

        if op == "b3" and len(parts) >= 6:
            name_idx = int(parts[2])
            city_id = int(parts[3])
            district_id = int(parts[4])
            page = int(parts[5])
            if await _catalog_render_positions_list(
                call, name_idx, city_id, district_id, page
            ):
                await call.answer()
            else:
                await call.answer()
            return

    except (ValueError, IndexError):
        await call.answer("Ошибка навигации", show_alert=True)
        return

    await call.answer()


@dp.callback_query(F.data.startswith("bprd:"))
async def back_to_product_card(call: CallbackQuery):
    _promo_cache.pop(call.from_user.id, None)
    product_id = int(call.data.split(":")[1])
    content = _product_card_content(
        product_id,
        back_callback_data=_catalog_back_for_product(product_id),
    )
    if not content:
        await call.answer("Позиция не найдена", show_alert=True)
        return
    text, markup = content
    await call.message.edit_text(text, reply_markup=markup)
    await call.answer()


@dp.callback_query(F.data.startswith("pay:"))
async def pay_ask_promo(call: CallbackQuery):
    _promo_cache.pop(call.from_user.id, None)
    product_id = int(call.data.split(":")[1])
    product = db.get_product(product_id)
    if not product:
        await call.answer("Позиция не найдена", show_alert=True)
        return
    text = (
        f"📂 {product['assortment_name']}\n"
        f"📌 {product['title']}\n"
        f"📏 {_format_qty_line(product)}\n"
        f"💰 Цена: {product['price']:.2f} RUB\n\n"
        "🎫 У вас есть промокод?"
    )
    await call.message.edit_text(text, reply_markup=keyboards.promo_ask_keyboard(product_id))
    await call.answer()


@dp.callback_query(F.data.startswith("promo:skip:"))
async def promo_skip(call: CallbackQuery):
    product_id = int(call.data.split(":")[2])
    _promo_cache.pop(call.from_user.id, None)
    await pay_offer_methods_after_promo(call, product_id, 0, None)


@dp.callback_query(F.data.startswith("promo:enter:"))
async def promo_enter_ask(call: CallbackQuery, state: FSMContext):
    product_id = int(call.data.split(":")[2])
    await state.set_state(UserStates.promo_enter)
    await state.update_data(promo_product_id=product_id)
    await call.message.edit_text("🎫 Введите промокод:")
    await call.answer()


@dp.message(StateFilter(UserStates.promo_enter))
async def promo_enter_text(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    promo = db.get_promo(code)
    if not promo:
        await message.answer("❌ Промокод не найден или истёк. Попробуйте другой или нажмите /cancel.")
        return
    data = await state.get_data()
    product_id = data.get("promo_product_id")
    await state.clear()
    product = db.get_product(product_id)
    if not product:
        await message.answer("Позиция не найдена")
        return
    discount_pct = int(promo["discount_percent"])
    _promo_cache[message.from_user.id] = {
        "product_id": product_id,
        "promo_code": promo["code"],
        "discount_percent": discount_pct,
    }
    await pay_offer_methods_after_promo(message, product_id, discount_pct, promo["code"])


async def pay_offer_methods_after_promo(obj, product_id: int, discount_pct: int, promo_code: Optional[str]):
    product = db.get_product(product_id)
    if not product:
        text = "Позиция не найдена"
        await (obj.answer(text) if isinstance(obj, Message) else obj.answer(text, show_alert=True))
        return
    district = db.get_district(product["district_id"])
    city = db.get_city(district["city_id"]) if district else None
    bal = db.get_user_balance(obj.from_user.id)
    price = float(product["price"])
    discount = price * discount_pct / 100 if discount_pct > 0 else 0
    final_price = price - discount
    show_bal = bal + 1e-9 >= final_price
    show_usdt = bool(shop_crypto_usdt_trc20())
    show_btc = bool(shop_crypto_btc())
    promo_line = f"\n🎫 Промокод: {promo_code} (-{discount_pct}%)" if promo_code else ""
    text = (
        f"📂 {product['assortment_name']}\n"
        f"📌 {product['title']}\n"
        f"📏 {_format_qty_line(product)}\n"
        f"Город: {city['name'] if city else '-'}\n"
        f"Район: {district['name'] if district else '-'}\n"
        f"💰 Цена: {price:.2f} RUB"
        f"{promo_line}"
        f"\n💵 Итого: {final_price:.2f} RUB\n"
        f"💎 На балансе: {bal:.2f} ₽\n\n"
        "Выберите способ оплаты."
        + ("\n💰 С баланса — мгновенное списание." if show_bal
           else "\nПополните баланс в «Мой кабинет», если не хватает средств.")
    )
    if isinstance(obj, Message):
        await obj.answer(text, reply_markup=keyboards.pay_method_keyboard(
            product_id, show_balance=show_bal, show_usdt=show_usdt, show_btc=show_btc,
        ))
    else:
        await obj.message.edit_text(text, reply_markup=keyboards.pay_method_keyboard(
            product_id, show_balance=show_bal, show_usdt=show_usdt, show_btc=show_btc,
        ))
    await send_event_log(
        obj.from_user,
        f"Оплата: {_product_label(product)} ({price:.2f} RUB)"
        + (f", промо {promo_code} (-{discount_pct}%)" if promo_code else ""),
    )


@dp.callback_query(F.data.startswith("paym:"))
async def pay_show_requisites(call: CallbackQuery):
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer()
        return
    method, product_id_s = parts[1], parts[2]
    product_id = int(product_id_s)
    product = db.get_product(product_id)
    if not product:
        await call.answer("Позиция не найдена", show_alert=True)
        return
    uid = call.from_user.id
    promo_info = _promo_cache.pop(uid, None)
    discount_pct = 0
    promo_code = None
    if promo_info and promo_info.get("product_id") == product_id:
        discount_pct = promo_info.get("discount_percent", 0)
        promo_code = promo_info.get("promo_code")
    price = float(product["price"])
    discount = price * discount_pct / 100 if discount_pct > 0 else 0
    amount = price - discount
    if method == "balance":
        order_id = db.create_order_paid_by_balance(
            user_id=uid,
            username=call.from_user.username,
            chat_id=call.message.chat.id,
            product_id=product_id,
            amount=amount,
        )
        if order_id is None:
            await call.answer("Недостаточно средств на балансе", show_alert=True)
            return
        if promo_code:
            db.use_promo(promo_code)
            db.set_order_promo(order_id, promo_code, discount)
        await send_event_log(
            call.from_user,
            f"Заказ #{order_id}: оплата с баланса «{_product_label(product)}», {amount:.2f} RUB"
            + (f" (промо {promo_code} -{discount_pct}%)" if promo_code else ""),
        )
        await _finalize_after_balance_payment(call, order_id, product)
        return
    if method == "card":
        req = db.pick_next_payment_card()
        pay_method = "card"
        header = "💳 Оплата переводом на карту"
        requisite_id = req["id"] if req else None
        requisite_line = req["details"] if req else ""
    elif method == "sbp":
        req = db.pick_next_payment_sbp()
        pay_method = "sbp"
        header = "💠 Оплата через СБП"
        requisite_id = req["id"] if req else None
        requisite_line = req["details"] if req else ""
    elif method == "usdt":
        req = True
        pay_method = "usdt"
        header = "💚 Оплата криптовалютой USDT (TRC20)"
        requisite_id = None
        requisite_line = _purchase_requisite_for_method("usdt", amount, call.from_user.id) or ""
    elif method == "btc":
        req = True
        pay_method = "btc"
        header = "🟠 Оплата криптовалютой Bitcoin (BTC)"
        requisite_id = None
        requisite_line = _purchase_requisite_for_method("btc", amount, call.from_user.id) or ""
    else:
        await call.answer()
        return
    if not req:
        await call.answer(
            "Реквизиты не настроены. Напишите администратору.",
            show_alert=True,
        )
        return
    order_id = db.create_order(
        user_id=uid,
        username=call.from_user.username,
        chat_id=call.message.chat.id,
        product_id=product_id,
        pay_method=pay_method,
        requisite_id=requisite_id,
        requisite_text=requisite_line,
        amount=amount,
        status="awaiting_payment",
    )
    if promo_code:
        db.use_promo(promo_code)
        db.set_order_promo(order_id, promo_code, discount)
    promo_line_banner = f"\n🎫 Промокод: {promo_code} (-{discount_pct}%)" if promo_code else ""
    text = (
        f"{header}\n\n"
        f"📦 {_product_label(product)}\n"
        f"💰 Цена: {price:.2f} RUB"
        f"{promo_line_banner}"
        f"\n💵 К оплате: {amount:.2f} RUB\n\n"
        f"Реквизит:\n{requisite_line}\n\n"
        "После перевода нажмите «Я оплатил». "
        f"Вопросы: {shop_support_contact()}"
    )
    await call.message.edit_text(
        text,
        reply_markup=keyboards.order_paid_keyboard(order_id, product_id),
    )
    await call.answer()
    await send_event_log(
        call.from_user,
        f"Выдан реквизит ({pay_method}) для заказа #{order_id}, товар {_product_label(product)}",
    )


@dp.callback_query(F.data.startswith("opaid:"))
async def user_mark_order_paid(call: CallbackQuery):
    order_id = int(call.data.split(":")[1])
    order = db.get_order(order_id)
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order["user_id"] != call.from_user.id:
        await call.answer("Это не ваш заказ", show_alert=True)
        return
    if order["status"] != "awaiting_payment":
        await call.answer("Заявка уже обработана", show_alert=True)
        return
    if order.get("pay_method") == "balance":
        await call.answer("Заказ уже оплачен с баланса", show_alert=True)
        return
    product = db.get_product(order["product_id"])
    product_name = _product_label(product) if product else f"id {order['product_id']}"
    auto_url = (product or {}).get("auto_delivery_url")
    if auto_url and str(auto_url).strip():
        url = str(auto_url).strip()
        if not db.complete_order_from_awaiting(order_id, url):
            await call.answer("Не удалось оформить выдачу", show_alert=True)
            return
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(
            "Оплата засчитана. Ваш товар:\n\n" + url,
            reply_markup=keyboards.order_review_invite_markup(order_id),
        )
        await call.answer()
        await send_event_log(
            call.from_user,
            f"Заказ #{order_id}: автовыдача «{product_name}»",
        )
        return
    db.set_order_status(order_id, "pending_confirm")
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "Заявка отправлена на проверку. После подтверждения оплаты вы получите ссылки."
    )
    await call.answer()
    await notify_pending_order(order_id, call.from_user, product_name)


@dp.callback_query(F.data.startswith("ocr:"))
async def admin_reject_order(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    order_id = int(call.data.split(":")[1])
    order = db.get_order(order_id)
    if not order or order["status"] != "pending_confirm":
        await call.answer("Заказ не найден или уже обработан", show_alert=True)
        return
    was_balance = order.get("pay_method") == "balance"
    amt = float(order["amount"])
    if was_balance:
        db.refund_order_balance(int(order["user_id"]), amt)
    db.set_order_status(order_id, "cancelled")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(f"Заказ #{order_id} отклонён.")
    await call.answer()
    user_msg = (
        f"Заказ отклонён. {amt:.2f} ₽ возвращены на ваш баланс."
        if was_balance
        else (
            "Оплата по заказу не подтверждена. Если средства списались — напишите в поддержку: "
            f"{shop_support_contact()}"
        )
    )
    try:
        await bot.send_message(order["chat_id"], user_msg)
    except Exception:
        logging.exception("Failed to notify user about rejection")


@dp.callback_query(F.data.startswith("ocf:"))
async def admin_confirm_order_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    order_id = int(call.data.split(":")[1])
    order = db.get_order(order_id)
    if not order or order["status"] != "pending_confirm":
        await call.answer("Заказ не найден или уже обработан", show_alert=True)
        return
    await state.set_state(AdminStates.order_delivery_input)
    await state.update_data(order_id=order_id, delivery_draft=None)
    await call.message.answer(
        f"Заказ #{order_id} — выдача.\n\n"
        "Пришлите одним сообщением:\n"
        "• ссылку (можно несколько строк подряд)\n"
        "• с новой строки — краткое описание для покупателя\n\n"
        "Дальше появится превью и кнопка «Выдать покупателю».",
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.order_delivery_input))
async def admin_delivery_receive_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.clear()
        return
    order = db.get_order(order_id)
    if not order or order["status"] != "pending_confirm":
        await state.clear()
        await message.answer("Заказ уже недоступен.")
        return
    draft = (message.text or "").strip()
    if not draft:
        await message.answer("Текст не должен быть пустым. Пришлите ссылку и описание.")
        return
    await state.update_data(delivery_draft=draft)
    await state.set_state(AdminStates.order_delivery_confirm)
    preview = draft if len(draft) <= 3500 else draft[:3497] + "..."
    await message.answer(
        f"Заказ #{order_id} — проверьте текст для покупателя:\n\n{preview}\n\n"
        "Если всё верно — нажмите «Выдать покупателю».",
        reply_markup=keyboards.delivery_confirm_keyboard(),
    )


@dp.message(StateFilter(AdminStates.order_delivery_confirm))
async def admin_delivery_waiting_button(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    await message.answer(
        "Сейчас ждём нажатия кнопки под превью: «Выдать покупателю», «Изменить текст» или «Отмена»."
    )


@dp.callback_query(F.data == "dlv_ok", StateFilter(AdminStates.order_delivery_confirm))
async def admin_delivery_confirm_send(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    draft = (data.get("delivery_draft") or "").strip()
    if not order_id or not draft:
        await state.clear()
        await call.answer("Нет данных выдачи", show_alert=True)
        return
    if not db.complete_order(order_id, draft):
        await state.clear()
        await call.answer("Заказ уже обработан", show_alert=True)
        return
    order = db.get_order(order_id)
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(f"Заказ #{order_id} выдан покупателю.")
    await call.answer("Готово")
    if order:
        buyer_label = (
            f"@{order['username']}"
            if order.get("username")
            else f"id {order['user_id']}"
        )
        await post_order_delivery_audit_to_log_chat(order_id, draft, buyer_label)
        try:
            await bot.send_message(
                order["chat_id"],
                "✅ Оплата подтверждена. Ваш заказ:\n\n" + draft,
                reply_markup=keyboards.order_review_invite_markup(order_id),
            )
        except Exception:
            logging.exception("Failed to deliver order to user")


@dp.callback_query(F.data == "dlv_edit", StateFilter(AdminStates.order_delivery_confirm))
async def admin_delivery_edit_again(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.clear()
        await call.answer()
        return
    await state.set_state(AdminStates.order_delivery_input)
    await state.update_data(order_id=order_id, delivery_draft=None)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        f"Заказ #{order_id}: пришлите заново ссылку и описание одним сообщением."
    )
    await call.answer()


@dp.callback_query(
    F.data == "dlv_cancel",
    StateFilter(AdminStates.order_delivery_confirm, AdminStates.order_delivery_input),
)
async def admin_delivery_cancel_flow(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("Выдача отменена. Заказ по-прежнему ждёт подтверждения в списке.")
    await call.answer()


_ORDER_STATUS_RU = {
    "awaiting_payment": "ожидает оплаты",
    "pending_confirm": "на проверке",
    "completed": "завершён",
    "cancelled": "отменён",
}


async def _send_support_ticket_to_log_chat(
    user: types.User, topic: str, body: str, context: str
) -> None:
    if not getattr(config, "LOG_CHAT_ID", None):
        return
    uname = f"@{user.username}" if user.username else "без username"
    text = (
        f"🆘 Обращение в поддержку ({topic})\n"
        f"UID:{user.id}\n"
        f"Пользователь: {user.full_name} ({uname})\n"
        f"Контекст: {context}\n\n"
        f"{body}\n\n"
        "↩️ Ответьте реплаем на это сообщение — бот отправит ответ пользователю."
    )
    tid = _forum_topic_id()
    kw: dict = {"chat_id": int(config.LOG_CHAT_ID), "text": text}
    if tid is not None:
        kw["message_thread_id"] = tid
    try:
        sent = await bot.send_message(**kw)
        db.add_support_ticket(int(sent.message_id), int(user.id))
    except Exception:
        logging.exception("Failed to send support ticket to log chat")


@dp.message(F.text.in_({"Поддержка", "📞 Поддержка"}))
async def support(message: Message):
    await message.answer(
        "Поддержка\n\nВыберите тип обращения:",
        reply_markup=keyboards.support_root_inline(),
    )


@dp.callback_query(F.data == "sup:q")
async def support_question_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.support_wait_text)
    await state.update_data(support_topic="Вопрос", support_context="общий вопрос")
    await call.answer()
    await call.message.answer("Опишите ваш вопрос одним сообщением.\n/cancel — отмена.")


@dp.callback_query(F.data == "sup:p")
async def support_problem_type(call: CallbackQuery):
    await call.answer()
    await call.message.answer(
        "Выберите, с чем проблема:",
        reply_markup=keyboards.support_problem_inline(),
    )


@dp.callback_query(F.data == "sup:po")
async def support_problem_order(call: CallbackQuery, state: FSMContext):
    orders = db.list_user_orders(call.from_user.id, limit=1)
    if not orders:
        await call.answer("У вас пока нет заказов.", show_alert=True)
        await call.message.answer(
            "📦 Проблема с заказом\n\nУ вас пока нет заказов, поэтому эту ветку открыть нельзя."
        )
        return
    await state.set_state(UserStates.support_wait_text)
    await state.update_data(support_topic="Проблема", support_context="с заказом")
    await call.answer()
    await call.message.answer(
        "Опишите проблему с заказом (желательно укажите номер заказа #...).\n"
        "/cancel — отмена."
    )


@dp.callback_query(F.data == "sup:pt")
async def support_problem_topup(call: CallbackQuery, state: FSMContext):
    if not db.has_user_topup_activity(call.from_user.id):
        await call.answer("У вас пока нет пополнений.", show_alert=True)
        await call.message.answer(
            "💵 Проблема с пополнением\n\n"
            "У вас пока нет пополнений или заявок на пополнение."
        )
        return
    await state.set_state(UserStates.support_wait_text)
    await state.update_data(
        support_topic="Проблема", support_context="с пополнением"
    )
    await call.answer()
    await call.message.answer(
        "Опишите проблему с пополнением (желательно укажите сумму/время).\n"
        "/cancel — отмена."
    )


@dp.message(StateFilter(UserStates.support_wait_text), F.text)
async def support_capture_text(message: Message, state: FSMContext):
    data = await state.get_data()
    topic = str(data.get("support_topic") or "Обращение")
    context = str(data.get("support_context") or "без контекста")
    body = (message.text or "").strip()
    if not body:
        await message.answer("Пустое сообщение. Опишите вопрос/проблему текстом.")
        return
    if len(body) > 3500:
        await message.answer("Слишком длинно. Не более 3500 символов.")
        return
    await _send_support_ticket_to_log_chat(message.from_user, topic, body, context)
    await state.clear()
    await message.answer("✅ Обращение отправлено. Ответ придёт сюда в чат.")


@dp.message(StateFilter(UserStates.support_wait_text))
async def support_capture_non_text(message: Message):
    await message.answer("Пожалуйста, отправьте обращение текстом.")




@dp.message(F.text.in_({"Поддержка по платежам", "💳 Поддержка по платежам"}))
async def payment_support_user(message: Message):
    await message.answer(f"Поддержка по платежам: {shop_payment_support()}")


_MONTH_NAMES_RU = {
    "01": "Январь", "02": "Февраль", "03": "Март", "04": "Апрель",
    "05": "Май", "06": "Июнь", "07": "Июль", "08": "Август",
    "09": "Сентябрь", "10": "Октябрь", "11": "Ноябрь", "12": "Декабрь",
}


def _month_label(month_key: str) -> str:
    parts = month_key.split("-")
    if len(parts) != 2:
        return month_key
    m = _MONTH_NAMES_RU.get(parts[1], parts[1])
    return f"{m} {parts[0]}"


def _build_reviews_text(rows: list, month_label: str, page: int) -> str:
    if not rows:
        return f"⭐ Отзывы ({month_label})\n\nНет отзывов за этот период."
    lines = [f"⭐ Отзывы ({month_label}) — стр. {page}", ""]
    for i, r in enumerate(rows, start=1):
        txt = (r.get("review_text") or "").strip()
        if len(txt) > 700:
            txt = txt[:697] + "..."
        city = r.get("city_name") or "—"
        pos = r.get("product_title") or "(позиция удалена)"
        date_raw = str(r.get("published_at") or "")
        date_part = date_raw[:10] if len(date_raw) >= 10 else "—"
        rv = int(r.get("review_rating") or 0)
        stars = "⭐" * rv if 1 <= rv <= 5 else "—"
        lines.extend([
            f"{i}) {city} / {pos}",
            f"   {stars} · {date_part}",
            f"   {txt}",
            "",
        ])
    return "\n".join(lines).strip()


async def _send_reviews_with_keyboard(
    target, month_key: str, page: int, total_pages: int, months: list
) -> None:
    rows = db.list_reviews_by_month(month_key, page=page, per_page=5)
    label = _month_label(month_key)
    text = _build_reviews_text(rows, label, page)
    keys = [m["month"] for m in months]
    cur_idx = keys.index(month_key) if month_key in keys else -1
    prev_month = keys[cur_idx + 1] if 0 <= cur_idx < len(keys) - 1 else None
    next_month = keys[cur_idx - 1] if cur_idx > 0 else None
    kb = keyboards.reviews_month_keyboard(month_key, page, total_pages, prev_month, next_month)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=kb)
    else:
        await target.message.edit_text(text, reply_markup=kb)


@dp.message(F.text.in_({"Отзывы", "⭐ Отзывы"}))
async def reviews(message: Message):
    months = db.list_review_months()
    if not months:
        await message.answer("⭐ Отзывы:\n\nПока нет отзывов.")
        return
    first = months[0]
    month_key = first["month"]
    total = int(first["total"])
    total_pages = max(1, (total + 4) // 5)
    await _send_reviews_with_keyboard(message, month_key, 1, total_pages, months)


@dp.callback_query(F.data == "rv:z")
async def reviews_dummy(call: CallbackQuery):
    await call.answer()


@dp.callback_query(F.data == "rv:menu")
async def reviews_back_menu(call: CallbackQuery):
    await call.answer()
    months = db.list_review_months()
    if not months:
        await call.message.edit_text("⭐ Отзывы:\n\nПока нет отзывов.")
        return
    first = months[0]
    month_key = first["month"]
    total = int(first["total"])
    total_pages = max(1, (total + 4) // 5)
    await _send_reviews_with_keyboard(call, month_key, 1, total_pages, months)


@dp.callback_query(F.data.startswith("rvp:"))
async def reviews_page(call: CallbackQuery):
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer()
        return
    month_key = parts[1]
    page = int(parts[2])
    months = db.list_review_months()
    total = 0
    for m in months:
        if m["month"] == month_key:
            total = int(m["total"])
            break
    if total == 0:
        await call.answer("Нет отзывов", show_alert=True)
        return
    total_pages = max(1, (total + 4) // 5)
    await _send_reviews_with_keyboard(call, month_key, page, total_pages, months)
    await call.answer()


@dp.callback_query(F.data.startswith("rvm:"))
async def reviews_month(call: CallbackQuery):
    month_key = call.data.split(":", 1)[1]
    months = db.list_review_months()
    total = 0
    for m in months:
        if m["month"] == month_key:
            total = int(m["total"])
            break
    if total == 0:
        await call.answer("Нет отзывов за этот месяц", show_alert=True)
        return
    total_pages = max(1, (total + 4) // 5)
    await _send_reviews_with_keyboard(call, month_key, 1, total_pages, months)
    await call.answer()


def _cabinet_screen_text(user: types.User) -> str:
    bal = db.get_user_balance(user.id)
    who = f"@{user.username}" if user.username else user.full_name
    return f"👤 {who}\n💵 Баланс: {bal:.0f} ₽"


async def _cabinet_reply_edit(
    call: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    """Редактирует сообщение кабинета; при ошибке — новое сообщение (как у пополнения)."""
    try:
        await call.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        low = str(e).lower()
        if "not modified" in low:
            return
        logging.warning("cabinet edit_text BadRequest: %s", e)
        try:
            await call.message.answer(text, reply_markup=reply_markup)
        except Exception:
            logging.exception("cabinet fallback answer after BadRequest")
    except Exception:
        logging.exception("cabinet edit_text failed")
        try:
            await call.message.answer(text, reply_markup=reply_markup)
        except Exception:
            logging.exception("cabinet fallback answer failed")


async def notify_topup_pending(request_id: int, buyer: types.User) -> None:
    req = db.get_topup_request(request_id)
    if not req:
        return
    uname = f"@{buyer.username}" if buyer.username else "без username"
    await send_event_log(
        buyer,
        f"Заявка на пополнение #{request_id}: {req['amount']:.0f} ₽",
    )
    await post_pending_topup_card_to_log_chat(request_id, buyer, req)

    method = _topup_method_label(req["requisite_text"])
    full_dm = (
        f"💵 Пополнение баланса #{request_id}\n"
        f"{buyer.full_name} ({uname}), id {buyer.id}\n"
        f"Сумма: {req['amount']:.0f} ₽ · {method}\n"
        f"Реквизит:\n{req['requisite_text']}\n\n"
        "Подтвердите после проверки платежа."
    )
    markup = keyboards.admin_topup_decide_markup(request_id)
    pcid = _payment_notify_chat_id()
    for aid in ADMIN_IDS:
        try:
            if pcid is not None:
                await bot.send_message(
                    aid,
                    (
                        f"📌 Пополнение #{request_id} ({req['amount']:.0f} ₽) — карточка "
                        f"с кнопками «Зачислить/Отклонить» в чате логов ({pcid})."
                    ),
                )
            else:
                await bot.send_message(aid, full_dm, reply_markup=markup)
        except Exception:
            logging.exception(
                "Не удалось уведомить админа %s о пополнении %s", aid, request_id
            )


@dp.message(F.text.in_({"Мой кабинет", "👤 Мой кабинет", "⚙️ Мой кабинет"}))
async def user_cabinet(message: Message):
    await message.answer(
        _cabinet_screen_text(message.from_user),
        reply_markup=keyboards.cabinet_main_inline(),
    )


@dp.callback_query(F.data == "cab:open")
async def cabinet_open(call: CallbackQuery):
    if call.from_user is None:
        await call.answer()
        return
    await call.answer()
    await _cabinet_reply_edit(
        call,
        _cabinet_screen_text(call.from_user),
        keyboards.cabinet_main_inline(),
    )


@dp.callback_query(F.data == "cab:topup")
async def cabinet_topup_menu(call: CallbackQuery):
    db.cancel_awaiting_topups_for_user(call.from_user.id)
    text = (
        "💳 Пополнение баланса\n\n"
        "Выберите сумму, затем способ: карта, СБП, USDT (TRC20) или BTC."
    )
    try:
        await call.message.edit_text(
            text,
            reply_markup=keyboards.cabinet_topup_amounts_inline(),
        )
    except Exception:
        logging.exception("cab:topup edit_text failed")
        await call.answer(
            "Не удалось открыть экран пополнения. Отправьте «Мой кабинет» ещё раз.",
            show_alert=True,
        )
        return
    await call.answer()


@dp.callback_query(F.data.startswith("cab:tamt:"))
async def cabinet_topup_amount(call: CallbackQuery):
    try:
        amount = float(call.data.split(":")[2])
    except (ValueError, IndexError):
        await call.answer("Ошибка суммы", show_alert=True)
        return
    if amount <= 0:
        await call.answer("Неверная сумма", show_alert=True)
        return
    if not _any_topup_method_available():
        await call.answer(
            "Способы пополнения не настроены. Напишите в поддержку.",
            show_alert=True,
        )
        return
    db.cancel_awaiting_topups_for_user(call.from_user.id)
    flags = _topup_method_flags()
    amt_i = int(amount)
    await call.message.edit_text(
        f"💵 Пополнение на {amt_i} ₽\n\nВыберите способ оплаты:",
        reply_markup=keyboards.cabinet_topup_methods_inline(amt_i, flags),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("cab:tmeth:"))
async def cabinet_topup_method(call: CallbackQuery):
    parts = call.data.split(":")
    if len(parts) != 4:
        await call.answer()
        return
    try:
        amount = float(parts[2])
    except ValueError:
        await call.answer("Ошибка суммы", show_alert=True)
        return
    kind = parts[3]
    if kind not in {"card", "sbp", "usdt", "btc"}:
        await call.answer()
        return
    if amount <= 0:
        await call.answer("Неверная сумма", show_alert=True)
        return
    req_text = _topup_requisite_for_method(kind, amount, call.from_user.id)
    if not req_text:
        hints = {
            "card": "Нет сохранённых карт — добавьте в админке.",
            "sbp": "СБП не добавлено — добавьте в админке.",
            "usdt": "Адрес USDT TRC20 не задан (💰 Оплата → 💚 USDT).",
            "btc": "Адрес BTC не задан (💰 Оплата → 🟠 BTC).",
        }
        await call.answer(hints[kind], show_alert=True)
        return
    rid = db.create_topup_request(call.from_user.id, amount, req_text)
    amt_i = int(amount)
    text = (
        f"💵 Пополнение на {amt_i} ₽\n\n"
        f"{req_text}\n\n"
        "После оплаты нажмите «✅ Я оплатил» — администратор проверит платёж и зачислит баланс."
    )
    await call.message.edit_text(
        text,
        reply_markup=keyboards.cabinet_topup_paid_inline(rid, amt_i),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("cab:tclaim:"))
async def cabinet_topup_claim(call: CallbackQuery):
    try:
        rid = int(call.data.split(":")[2])
    except (ValueError, IndexError):
        await call.answer()
        return
    req = db.get_topup_request(rid)
    if not req or req["user_id"] != call.from_user.id:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    if req["status"] != "awaiting_claim":
        await call.answer("Заявка уже отправлена на проверку", show_alert=True)
        return
    if not db.submit_topup_claim(rid, call.from_user.id):
        await call.answer("Не удалось отправить", show_alert=True)
        return
    try:
        await call.message.edit_text(
            f"Заявка #{rid} на {req['amount']:.0f} ₽ отправлена на проверку.\n"
            "После зачисления баланс обновится в «Мой кабинет».",
            reply_markup=keyboards.cabinet_back_to_menu_inline(),
        )
    except Exception:
        logging.exception("cab:tclaim edit_text failed")
        await call.answer(
            "Заявка отправлена, но экран не обновился. Загляните в «Мой кабинет».",
            show_alert=True,
        )
    else:
        await call.answer("Отправлено администратору")
    await notify_topup_pending(rid, call.from_user)


@dp.callback_query(F.data == "cab:ref")
async def cabinet_referral(call: CallbackQuery):
    if call.from_user is None:
        await call.answer()
        return
    await call.answer()
    uid = call.from_user.id
    try:
        me = await bot.get_me()
        bot_username = me.username if me else None
    except Exception:
        logging.exception("cab:ref get_me")
        bot_username = None
    if bot_username:
        link_line = f"https://t.me/{bot_username}?start=ref_{uid}"
    else:
        link_line = "Задайте username боту в BotFather — тогда появится ссылка."
    invited = db.count_referrals(uid)
    text = (
        "🌶️ Реферальная система\n\n"
        f"Ваша ссылка для приглашений:\n{link_line}\n\n"
        f"Переходов по вашей ссылке (записано в боте): {invited}\n\n"
        "Награды по рефералам настраиваются отдельно — уточняйте у администрации."
    )
    await _cabinet_reply_edit(
        call,
        text,
        keyboards.cabinet_back_to_menu_inline(),
    )


@dp.callback_query(F.data == "cab:orders")
async def cabinet_orders(call: CallbackQuery):
    if call.from_user is None:
        await call.answer()
        return
    await call.answer()
    orders = db.list_user_orders(call.from_user.id, limit=15)
    if not orders:
        body = "У вас пока нет заказов."
        mk = keyboards.cabinet_back_to_menu_inline()
    else:
        lines = []
        for o in orders:
            st = _ORDER_STATUS_RU.get(o["status"], o["status"])
            rmar = ""
            if o.get("status") == "completed":
                if (o.get("review_text") or "").strip():
                    rmar = " · ⭐ отзыв есть"
                else:
                    rmar = " · можно отзыв"
            lines.append(
                f"#{o['id']} · {o['product_name']} · {o['amount']:.2f} ₽ · {st}{rmar}"
            )
        body = "📦 Мои заказы:\n\n" + "\n".join(lines)
        if any(
            o.get("status") == "completed"
            and not (o.get("review_text") or "").strip()
            for o in orders
        ):
            body += (
                "\n\nНажмите «⭐ Отзыв №…» ниже или кнопку под сообщением о выдаче."
            )
        mk = keyboards.cabinet_orders_markup(orders)
    await _cabinet_reply_edit(call, body, mk)


@dp.callback_query(F.data.startswith("revgo:"))
async def user_review_start(call: CallbackQuery, state: FSMContext):
    try:
        order_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer()
        return
    order = db.get_order(order_id)
    if not order or order["user_id"] != call.from_user.id:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order["status"] != "completed":
        await call.answer(
            "Отзыв можно оставить только по завершённому заказу.",
            show_alert=True,
        )
        return
    if (order.get("review_text") or "").strip():
        await call.answer("По этому заказу отзыв уже оставлен.", show_alert=True)
        return
    await state.set_state(UserStates.order_review_rating)
    await state.update_data(review_order_id=order_id)
    await call.answer()
    await call.message.answer(
        f"Заказ #{order_id}. Сначала выберите оценку:",
        reply_markup=keyboards.order_review_rating_markup(order_id),
    )


@dp.callback_query(
    F.data.startswith("revr:"),
    StateFilter(UserStates.order_review_rating),
)
async def user_review_pick_rating(call: CallbackQuery, state: FSMContext):
    try:
        _, order_id_s, rating_s = call.data.split(":")
        order_id = int(order_id_s)
        rating = int(rating_s)
    except (ValueError, IndexError):
        await call.answer("Некорректная оценка", show_alert=True)
        return
    if rating < 1 or rating > 5:
        await call.answer("Оценка должна быть от 1 до 5", show_alert=True)
        return
    data = await state.get_data()
    st_order_id = int(data.get("review_order_id") or 0)
    if st_order_id != order_id:
        await call.answer("Сессия устарела. Нажмите «Оставить отзыв» заново.", show_alert=True)
        return
    order = db.get_order(order_id)
    if not order or order["user_id"] != call.from_user.id:
        await state.clear()
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order["status"] != "completed" or (order.get("review_text") or "").strip():
        await state.clear()
        await call.answer("По этому заказу отзыв уже недоступен.", show_alert=True)
        return
    await state.update_data(review_rating=rating)
    await state.set_state(UserStates.order_review_text)
    await call.answer()
    await call.message.answer(
        f"Оценка: {'⭐' * rating}\n"
        "Теперь напишите отзыв одним сообщением (до 3500 символов).\n"
        "/cancel — отмена."
    )


@dp.message(StateFilter(UserStates.order_review_text), F.text)
async def user_review_submit(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("review_order_id")
    rating = int(data.get("review_rating") or 0)
    if not order_id:
        await state.clear()
        return
    if rating < 1 or rating > 5:
        await state.clear()
        await message.answer("Сначала выберите оценку кнопками.")
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пустой текст. Напишите отзыв или /cancel.")
        return
    if len(raw) > 3500:
        await message.answer("Слишком длинно. Не больше 3500 символов.")
        return
    oid = int(order_id)
    if not db.save_order_review(oid, message.from_user.id, raw, rating):
        await state.clear()
        await message.answer(
            "Не удалось сохранить: заказ недоступен или отзыв уже оставлен."
        )
        return
    await state.clear()
    await message.answer(f"Спасибо, отзыв сохранён. Оценка: {'⭐' * rating}")
    order = db.get_order(oid)
    product_line = "?"
    if order:
        product = db.get_product(order["product_id"])
        product_line = (
            _product_label(product) if product else f"id {order['product_id']}"
        )
    await post_review_to_log_chat(oid, message.from_user, raw, product_line)


@dp.message(StateFilter(UserStates.order_review_text))
async def user_review_non_text(message: Message):
    await message.answer("Отзыв нужно отправить текстом. /cancel — отмена.")


@dp.message(StateFilter(UserStates.order_review_rating))
async def user_review_wait_rating(message: Message):
    await message.answer("Сначала выберите оценку кнопками 1-5 ниже.")


@dp.callback_query(F.data == "cab:hist")
async def cabinet_history(call: CallbackQuery):
    if call.from_user is None:
        await call.answer()
        return
    await call.answer()
    rows = db.list_balance_topups(call.from_user.id, limit=15)
    if not rows:
        body = (
            "💰 Движение по балансу\n\n"
            "Пока нет операций."
        )
    else:
        lines = []
        for r in rows:
            amt = float(r["amount"])
            src = r.get("source") or "topup"
            sign = "+" if amt > 0 else ""
            lines.append(f"{sign}{amt:.0f} ₽ · {src} · #{r['id']}")
        body = "💰 Движение по балансу:\n\n" + "\n".join(lines)
    await _cabinet_reply_edit(
        call,
        body,
        keyboards.cabinet_back_to_menu_inline(),
    )


@dp.callback_query(F.data == "admin:topups_pending")
async def admin_topups_pending(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    pending = db.list_pending_topup_requests()
    if not pending:
        await call.message.answer("Нет заявок на пополнение баланса.")
        await call.answer()
        return
    for r in pending:
        uid = r["user_id"]
        m = _topup_method_label(r["requisite_text"])
        text = (
            f"💵 #{r['id']} · id {uid} · {r['amount']:.0f} ₽ · {m}\n"
            f"{r['requisite_text']}"
        )
        await call.message.answer(
            text,
            reply_markup=keyboards.admin_topup_decide_markup(r["id"]),
        )
    await call.answer()


@dp.callback_query(F.data.startswith("atu_ok:"))
async def admin_topup_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        rid = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer()
        return
    req = db.get_topup_request(rid)
    if not req or req["status"] != "pending_admin":
        await call.answer("Заявка не найдена или уже обработана", show_alert=True)
        return
    amt = float(req["amount"])
    uid = int(req["user_id"])
    if db.approve_topup_request(rid):
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await call.message.answer(f"Заявка #{rid} зачислена ({amt:.0f} ₽).")
        try:
            await bot.send_message(uid, f"✅ Баланс пополнен на {amt:.0f} ₽.")
        except Exception:
            logging.exception("Не удалось уведомить пользователя %s о пополнении", uid)
        await call.answer("Зачислено")
    else:
        await call.answer("Ошибка", show_alert=True)


@dp.callback_query(F.data.startswith("atu_no:"))
async def admin_topup_reject(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        rid = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer()
        return
    req = db.get_topup_request(rid)
    if not req or req["status"] != "pending_admin":
        await call.answer("Заявка не найдена или уже обработана", show_alert=True)
        return
    uid = int(req["user_id"])
    if db.reject_topup_request(rid):
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await call.message.answer(f"Заявка #{rid} отклонена.")
        try:
            await bot.send_message(
                uid,
                "❌ Заявка на пополнение баланса отклонена. Если деньги списались — напишите в поддержку.",
            )
        except Exception:
            logging.exception("Не удалось уведомить пользователя %s об отказе", uid)
        await call.answer("Отклонено")
    else:
        await call.answer("Ошибка", show_alert=True)





_WORK_DEPOSIT_AMOUNT = 5000


@dp.message(StateFilter(UserStates.work_city), F.text)
async def work_city_received(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if not city:
        await message.answer("Введите название города.")
        return
    await state.clear()
    _work_cache[message.from_user.id] = {"city": city}
    await message.answer(
        f"🌆 Город: {city}\n\n"
        f"💼 Работа — выберите вариант:\n\n"
        f"• Залог {_WORK_DEPOSIT_AMOUNT}₽ — доступ к работе\n"
        f"• Без залога — связь с оператором",
        reply_markup=keyboards.work_options_keyboard(),
    )


@dp.message(StateFilter(UserStates.work_city))
async def work_city_non_text(message: Message):
    await message.answer("Пожалуйста, введите название города текстом.")


@dp.callback_query(F.data == "work:operator")
async def work_operator(call: CallbackQuery):
    _work_cache.pop(call.from_user.id, None)
    contact = shop_support_contact()
    await call.message.edit_text(
        f"📞 Напишите боту поддержки:\n{contact}\n\n"
        "Выберите категорию «💼 Работа» и опишите ваш вопрос."
    )
    await call.answer()


@dp.callback_query(F.data == "work:deposit")
async def work_deposit_show_methods(call: CallbackQuery):
    city_data = _work_cache.get(call.from_user.id)
    if not city_data:
        await call.answer("Начните заново: 💼 Работа", show_alert=True)
        return
    await call.message.edit_text(
        f"💳 Залог {_WORK_DEPOSIT_AMOUNT}₽\n\n"
        f"🌆 Город: {city_data['city']}\n\n"
        "Выберите способ оплаты залога:",
        reply_markup=keyboards.work_pay_method_keyboard(),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("work:meth:"))
async def work_deposit_show_requisites(call: CallbackQuery):
    city_data = _work_cache.get(call.from_user.id)
    if not city_data:
        await call.answer("Начните заново: 💼 Работа", show_alert=True)
        return
    kind = call.data.split(":")[2]
    if kind not in {"card", "sbp", "usdt", "btc"}:
        await call.answer()
        return
    req_text = _topup_requisite_for_method(kind, _WORK_DEPOSIT_AMOUNT, call.from_user.id)
    if not req_text:
        hints = {
            "card": "Нет сохранённых карт — напишите в поддержку.",
            "sbp": "СБП не добавлено — напишите в поддержку.",
            "usdt": "Адрес USDT TRC20 не задан.",
            "btc": "Адрес BTC не задан.",
        }
        await call.answer(hints[kind], show_alert=True)
        return
    rid = db.create_topup_request(call.from_user.id, _WORK_DEPOSIT_AMOUNT, req_text)
    # Store city in work_cache linked to request
    _work_cache[call.from_user.id] = {"city": city_data["city"], "request_id": rid}
    text = (
        f"💳 Залог {_WORK_DEPOSIT_AMOUNT}₽\n\n"
        f"{req_text}\n\n"
        "После оплаты нажмите «✅ Я оплатил» — администратор проверит платёж."
    )
    await call.message.edit_text(
        text,
        reply_markup=keyboards.work_paid_keyboard(rid),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("work:claim:"))
async def work_claim_paid(call: CallbackQuery):
    try:
        rid = int(call.data.split(":")[2])
    except (ValueError, IndexError):
        await call.answer()
        return
    req = db.get_topup_request(rid)
    if not req or req["user_id"] != call.from_user.id:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    if req["status"] != "awaiting_claim":
        await call.answer("Заявка уже отправлена на проверку", show_alert=True)
        return
    if not db.submit_topup_claim(rid, call.from_user.id):
        await call.answer("Не удалось отправить", show_alert=True)
        return
    city_data = _work_cache.pop(call.from_user.id, {})
    city_str = city_data.get("city", "не указан")
    try:
        await call.message.edit_text(
            f"✅ Заявка на залог отправлена на проверку.\n"
            f"Город: {city_str}\n\n"
            "После подтверждения оператор свяжется с вами.",
            reply_markup=None,
        )
    except Exception:
        pass
    await call.answer("Отправлено администратору")
    # Notify admin with city info
    uid = call.from_user.id
    uname = f"@{call.from_user.username}" if call.from_user.username else "без username"
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"💼 Новая заявка на работу\n\n"
                f"Пользователь: {call.from_user.full_name} ({uname})\n"
                f"ID: {uid}\n"
                f"Город: {city_str}\n"
                f"Залог: {_WORK_DEPOSIT_AMOUNT}₽\n"
                f"Заявка на пополнение #{rid}\n\n"
                "Проверьте платёж в админке → 💰 Оплата → 💵 Пополнения баланса\n"
                "После подтверждения ответьте пользователю через бота поддержки.",
            )
        except Exception:
            logging.exception("Не удалось уведомить админа о заявке на работу")


@dp.message(F.text == "⚙️ Админка")
async def open_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await message.answer(
        "Админ-панель (inline-кнопки ниже):\n"
        "Сразу под «Настройки» — отдельная строка «📂 Ассортименты».\n"
        "Если кнопок не видно — пришлите ⚙️ Админка ещё раз после обновления бота.\n\n"
        "🛠 Настройки — приветствие, поддержка, отзывы.\n"
        "📦 Подтверждения оплат · 💰 Оплата — карты, заказы, балансы.\n"
        "📣 Рассылка — акции и объявления всем, кто есть в базе.\n"
        "👥 База и рефералы — сколько пользователей и реф-привязок.",
        reply_markup=keyboards.admin_menu(),
    )


@dp.callback_query(F.data == "admin:userbase")
async def admin_userbase_info(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    s = db.admin_userbase_snapshot()
    text = (
        "👥 База пользователей бота (для рассылки)\n\n"
        f"Всего записей в bot_users: {s['bot_users']}\n"
        "Туда попадают: нажали /start (после обновления бота), "
        "а также id из заказов, рефералов, балансов и заявок на пополнение "
        "(подтянулось при миграции БД).\n\n"
        "🌶️ Рефералы\n"
        f"Привязок «кто кого пригласил»: {s['referrals_total']}\n"
        f"Уникальных пригласивших (referrer): {s['referrers_distinct']}\n\n"
        "Для сравнения:\n"
        f"Уникальных покупателей в заказах: {s['orders_users_distinct']}\n"
        f"Строк в балансах: {s['balance_rows']}\n\n"
        "При новом переходе по реф-ссылке в чат логов уходит сообщение "
        "«кто пришёл» и «чей это реферал»."
    )
    await call.message.answer(text)
    await call.answer()


@dp.callback_query(F.data == "admin:promos")
async def admin_promos_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.answer("🎫 Управление промокодами", reply_markup=keyboards.admin_promos_keyboard())
    await call.answer()


@dp.callback_query(F.data == "admin:promo_add")
async def admin_promo_add_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.promo_add_name)
    await call.message.answer("Введите название промокода (буквы и цифры, например: SALE10):")
    await call.answer()


@dp.message(StateFilter(AdminStates.promo_add_name))
async def admin_promo_add_name(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    if not code or len(code) > 20:
        await message.answer("Некорректный код. Максимум 20 символов. Попробуйте снова или /cancel.")
        return
    await state.update_data(promo_code=code)
    await state.set_state(AdminStates.promo_add_percent)
    await message.answer("Введите процент скидки (от 1 до 100):")


@dp.message(StateFilter(AdminStates.promo_add_percent))
async def admin_promo_add_percent(message: Message, state: FSMContext):
    try:
        pct = int(message.text.strip())
        if pct < 1 or pct > 100:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 1 до 100. Попробуйте снова или /cancel.")
        return
    await state.update_data(promo_percent=pct)
    await state.set_state(AdminStates.promo_add_uses)
    await message.answer("Максимальное количество использований (0 — без лимита):")


@dp.message(StateFilter(AdminStates.promo_add_uses))
async def admin_promo_add_uses(message: Message, state: FSMContext):
    try:
        max_uses = int(message.text.strip())
        if max_uses < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите число >= 0. 0 — без лимита. Попробуйте снова или /cancel.")
        return
    data = await state.get_data()
    code = data["promo_code"]
    pct = data["promo_percent"]
    if db.create_promo(code, pct, max_uses):
        await message.answer(f"✅ Промокод {code} создан: -{pct}%, лимит: {'∞' if max_uses == 0 else max_uses}")
    else:
        await message.answer("❌ Ошибка: такой промокод уже существует.")
    await state.clear()


@dp.callback_query(F.data == "admin:promo_list")
async def admin_promo_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promos = db.list_promos()
    if not promos:
        await call.message.answer("Нет промокодов.")
        await call.answer()
        return
    await call.message.answer("Список промокодов (нажмите чтобы вкл/выкл или удалить):", reply_markup=keyboards.admin_promo_list_keyboard(promos))
    await call.answer()


@dp.callback_query(F.data.startswith("admin:promo_toggle:"))
async def admin_promo_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    promo_id = int(call.data.split(":")[2])
    db.toggle_promo(promo_id)
    promos = db.list_promos()
    await call.message.edit_text("✅ Статус изменён.", reply_markup=keyboards.admin_promo_list_keyboard(promos))
    await call.answer()


@dp.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_entry(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.broadcast_compose)
    n = db.count_bot_users()
    await call.message.answer(
        "📣 Рассылка пользователям\n\n"
        f"В базе сейчас {n} пользователей (кто нажимал /start или был в заказах/рефералах).\n\n"
        "Пришлите в этот чат одно сообщение — его получат все (копия 1:1: текст, фото с подписью, "
        "видео, документ и т.д.).\n\n"
        "/cancel — выйти без отправки.\n\n"
        "Рассылку лучше запускать из личного чата с ботом."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.broadcast_compose))
async def admin_broadcast_capture(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if message.content_type in _BROADCAST_SKIP_CONTENT:
        return
    if message.text and message.text.startswith("/"):
        await message.answer(
            "Это похоже на команду. Чтобы выйти — /cancel. "
            "Или пришлите обычное сообщение для рассылки."
        )
        return
    if message.content_type == ContentType.TEXT and not (message.text or "").strip():
        await message.answer("Пришлите непустой текст или сообщение с медиа.")
        return
    await state.update_data(
        bcst_from_chat=message.chat.id,
        bcst_msg_id=message.message_id,
    )
    await state.set_state(AdminStates.broadcast_confirm)
    n = db.count_bot_users()
    await message.answer(
        f"Сообщение принято. Получателей: {n}.\nОтправить всем?",
        reply_markup=keyboards.broadcast_confirm_markup(),
    )


@dp.callback_query(F.data == "bcst:x")
async def admin_broadcast_cancel(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    st = await state.get_state()
    if st != AdminStates.broadcast_confirm.state:
        await call.answer("Нечего отменять", show_alert=True)
        return
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("Рассылка отменена.")
    await call.answer()


@dp.callback_query(F.data == "bcst:go")
async def admin_broadcast_go(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    if await state.get_state() != AdminStates.broadcast_confirm.state:
        await call.answer("Сессия устарела. Начните снова: Админка → Рассылка.", show_alert=True)
        return
    data = await state.get_data()
    from_chat = data.get("bcst_from_chat")
    msg_id = data.get("bcst_msg_id")
    if from_chat is None or msg_id is None:
        await state.clear()
        await call.answer("Нет данных сообщения", show_alert=True)
        return
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.answer("Рассылка запущена")
    admin_id = call.from_user.id
    asyncio.create_task(
        _run_broadcast_task(admin_id, int(from_chat), int(msg_id)),
        name=f"broadcast:{admin_id}:{msg_id}",
    )


@dp.callback_query(F.data == "admin:settings_hub")
async def admin_settings_hub(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.answer(
        "Настройки (как в главном меню пользователя):\n"
        "«Поддержка», «Отзывы», «Поддержка по платежам» — ниже.\n"
        "Каталог: ассортимент (группа) → город → район → позиция с ценой.\n"
        "Ассортименты: не здесь — вернитесь в главное меню админки (⚙️ Админка) "
        "и нажмите «📂 Ассортименты» под Настройками.\n"
        "Позиция: district_id|Ассортимент|Название|Цена|[1шт / 1г]|ссылка_авто\n"
        "«О нас» / «Правила» — запасные тексты (на клавиатуре не показываются).",
        reply_markup=keyboards.settings_hub(),
    )
    await call.answer()


@dp.callback_query(F.data == "admin:set_welcome_text")
async def admin_set_welcome_text_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_welcome_text)
    await call.message.answer(
        "Текст приветствия для /start.\n"
        "Подставка: {name} — имя пользователя.\n"
        "Пришлите сообщение с новым текстом."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_welcome_text), F.text)
async def admin_set_welcome_text_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    t = (message.text or "").strip()
    if not t:
        await message.answer("Текст не должен быть пустым.")
        return
    db.set_setting("welcome_text", t)
    await message.answer("Текст приветствия сохранён.")
    await state.clear()


@dp.callback_query(F.data == "admin:set_welcome_photo")
async def admin_set_welcome_photo_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_welcome_photo)
    await call.message.answer(
        "Пришлите фото для /start.\n"
        "Подпись к фото (если есть) станет текстом приветствия; в подписи работает {name}."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_welcome_photo), F.photo)
async def admin_set_welcome_photo_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    file_id = message.photo[-1].file_id
    db.set_setting("welcome_photo_file_id", file_id)
    if message.caption and message.caption.strip():
        db.set_setting("welcome_text", message.caption.strip())
    await message.answer("Фото для /start сохранено.")
    await state.clear()


@dp.message(StateFilter(AdminStates.set_welcome_photo))
async def admin_set_welcome_photo_need_image(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Нужно прислать изображение (фото).")


@dp.callback_query(F.data == "admin:clear_welcome_photo")
async def admin_clear_welcome_photo(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    db.delete_setting("welcome_photo_file_id")
    await call.message.answer("Фото в /start отключено — останется только текст.")
    await call.answer()


@dp.callback_query(F.data == "admin:set_about")
async def admin_set_about_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_about_text)
    await call.message.answer(
        "Текст «О нас» (резерв, отдельной кнопки в меню нет — можно использовать в постах)."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_about_text), F.text)
async def admin_set_about_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    t = (message.text or "").strip()
    if not t:
        await message.answer("Текст не должен быть пустым.")
        return
    db.set_setting("about_text", t)
    await message.answer("Текст «О нас» (резерв) сохранён.")
    await state.clear()


@dp.callback_query(F.data == "admin:set_rules")
async def admin_set_rules_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_rules_text)
    await call.message.answer(
        "Текст «Правила» (резерв, отдельной кнопки в меню нет)."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_rules_text), F.text)
async def admin_set_rules_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    t = (message.text or "").strip()
    if not t:
        await message.answer("Текст не должен быть пустым.")
        return
    db.set_setting("rules_text", t)
    await message.answer("Текст «Правила» (резерв) сохранён.")
    await state.clear()


@dp.callback_query(F.data == "admin:set_support")
async def admin_set_support_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_support_contact)
    await call.message.answer(
        "Контакт для кнопки «Поддержка» в главном меню "
        "(@username, ссылка или телефон).\n"
        "Также подставляется в текст реквизитов и в сообщении при отказе в оплате."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_support_contact), F.text)
async def admin_set_support_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    t = (message.text or "").strip()
    if not t:
        await message.answer("Контакт не должен быть пустым.")
        return
    db.set_setting("support_contact", t)
    await message.answer("Контакт «Поддержка» сохранён.")
    await state.clear()


@dp.callback_query(F.data == "admin:set_pay_support")
async def admin_set_pay_support_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_pay_support_contact)
    await call.message.answer(
        "Контакт для кнопки «Поддержка по платежам» в главном меню "
        "(@username, ссылка или телефон)."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_pay_support_contact), F.text)
async def admin_set_pay_support_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    t = (message.text or "").strip()
    if not t:
        await message.answer("Контакт не должен быть пустым.")
        return
    db.set_setting("payment_support_contact", t)
    await message.answer("Контакт «Поддержка по платежам» сохранён.")
    await state.clear()


@dp.callback_query(F.data == "admin:set_reviews")
async def admin_set_reviews_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_reviews_body)
    await call.message.answer(
        "Текст для кнопки «Отзывы» в главном меню "
        "(ссылка на канал, пост или короткий текст)."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_reviews_body), F.text)
async def admin_set_reviews_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    t = (message.text or "").strip()
    if not t:
        await message.answer("Текст не должен быть пустым.")
        return
    db.set_setting("reviews_text", t)
    await message.answer("Текст кнопки «Отзывы» сохранён.")
    await state.clear()


@dp.callback_query(F.data == "admin:set_crypto_usdt")
async def admin_set_crypto_usdt_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_crypto_usdt)
    cur = shop_crypto_usdt_trc20()
    await call.message.answer(
        "Адрес USDT в сети TRC20 (Tron) для пополнения баланса.\n"
        "Одна строка. Пустое сообщение — сбросить адрес (только из БД; резерв в config останется).\n\n"
        f"Сейчас: {cur or '—'}"
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_crypto_usdt), F.text)
async def admin_set_crypto_usdt_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    t = (message.text or "").strip()
    if not t:
        db.delete_setting("crypto_usdt_trc20")
        await message.answer("Адрес USDT TRC20 в базе сброшен.")
    else:
        db.set_setting("crypto_usdt_trc20", t)
        await message.answer("Адрес USDT TRC20 сохранён.")
    await state.clear()


@dp.callback_query(F.data == "admin:set_crypto_btc")
async def admin_set_crypto_btc_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.set_crypto_btc)
    cur = shop_crypto_btc()
    await call.message.answer(
        "Адрес Bitcoin (BTC) для пополнения баланса.\n"
        "Одна строка. Пустое сообщение — сбросить адрес в базе.\n\n"
        f"Сейчас: {cur or '—'}"
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.set_crypto_btc), F.text)
async def admin_set_crypto_btc_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    t = (message.text or "").strip()
    if not t:
        db.delete_setting("crypto_btc")
        await message.answer("Адрес BTC в базе сброшен.")
    else:
        db.set_setting("crypto_btc", t)
        await message.answer("Адрес BTC сохранён.")
    await state.clear()


@dp.callback_query(F.data == "admin:payments_hub")
async def admin_payments_hub(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.answer(
        f"Оплата: карты/СБП (до {MAX_PAYMENT_CARDS}/{MAX_PAYMENT_SBP}), "
        "адреса 💚 USDT TRC20 и 🟠 BTC для пополнения баланса, "
        "ожидающие пополнения и ручные балансы. "
        "Заказы: карта/СБП или списание с баланса.",
        reply_markup=keyboards.payments_hub(),
    )
    await call.answer()


@dp.callback_query(F.data == "admin:balance_hub")
async def admin_balance_hub_open(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.answer(
        "👛 Балансы пользователей\n\n"
        "Форматы:\n"
        "• Зачислить / списать: user_id|сумма (рубли, можно с точкой)\n"
        "• Узнать баланс: отправьте только user_id (цифры).\n\n"
        "При отклонении заказа, оплаченного с баланса, сумма автоматически возвращается.",
        reply_markup=keyboards.admin_balance_hub(),
    )
    await call.answer()


@dp.callback_query(F.data == "admin:bal_credit")
async def admin_bal_credit_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.admin_bal_credit)
    await call.message.answer("Зачисление: user_id|сумма\nПример: 7998704133|500")
    await call.answer()


@dp.callback_query(F.data == "admin:bal_debit")
async def admin_bal_debit_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.admin_bal_debit)
    await call.message.answer("Списание: user_id|сумма\nПример: 7998704133|100")
    await call.answer()


@dp.callback_query(F.data == "admin:bal_lookup")
async def admin_bal_lookup_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.admin_bal_lookup)
    await call.message.answer("Введите user_id пользователя (Telegram id).")
    await call.answer()


@dp.message(StateFilter(AdminStates.admin_bal_credit), F.text)
async def admin_bal_credit_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    try:
        uid_s, amt_s = raw.split("|", 1)
        uid = int(uid_s.strip())
        amt = float(amt_s.replace(",", ".").strip())
    except ValueError:
        await message.answer("Формат: user_id|сумма")
        return
    if amt <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    try:
        db.admin_credit_balance(uid, amt)
    except ValueError:
        await message.answer("Неверная сумма.")
        return
    new_bal = db.get_user_balance(uid)
    await message.answer(f"Зачислено {amt:.2f} ₽ пользователю `{uid}`. Баланс: {new_bal:.2f} ₽.")
    await state.clear()


@dp.message(StateFilter(AdminStates.admin_bal_debit), F.text)
async def admin_bal_debit_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    try:
        uid_s, amt_s = raw.split("|", 1)
        uid = int(uid_s.strip())
        amt = float(amt_s.replace(",", ".").strip())
    except ValueError:
        await message.answer("Формат: user_id|сумма")
        return
    if amt <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    if db.admin_try_debit_balance(uid, amt):
        new_bal = db.get_user_balance(uid)
        await message.answer(f"Списано {amt:.2f} ₽ с `{uid}`. Баланс: {new_bal:.2f} ₽.")
    else:
        await message.answer(f"Не удалось списать: недостаточно средств или неверный user_id.")
    await state.clear()


@dp.message(StateFilter(AdminStates.admin_bal_lookup), F.text)
async def admin_bal_lookup_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Нужен числовой user_id.")
        return
    uid = int(raw)
    bal = db.get_user_balance(uid)
    await message.answer(f"Пользователь `{uid}`: баланс {bal:.2f} ₽.")
    await state.clear()


@dp.callback_query(F.data == "admin:add_pay_card")
async def admin_add_pay_card_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    if db.count_payment_cards() >= MAX_PAYMENT_CARDS:
        await call.answer(f"Уже максимум карт ({MAX_PAYMENT_CARDS}).", show_alert=True)
        return
    await state.set_state(AdminStates.add_pay_card)
    await call.message.answer(
        "Введите реквизиты карты одной строкой (номер, банк, ФИО — как удобно клиенту)."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.add_pay_card))
async def admin_add_pay_card_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Строка не должна быть пустой.")
        return
    if db.count_payment_cards() >= MAX_PAYMENT_CARDS:
        await message.answer(f"Уже максимум карт ({MAX_PAYMENT_CARDS}).")
        await state.clear()
        return
    if db.add_payment_card(text):
        await message.answer("Карта добавлена.")
    else:
        await message.answer("Не удалось добавить.")
    await state.clear()


@dp.callback_query(F.data == "admin:add_pay_sbp")
async def admin_add_pay_sbp_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    if db.count_payment_sbp() >= MAX_PAYMENT_SBP:
        await call.answer(f"Уже максимум СБП ({MAX_PAYMENT_SBP}).", show_alert=True)
        return
    await state.set_state(AdminStates.add_pay_sbp)
    await call.message.answer(
        "Введите реквизит СБП одной строкой (телефон, банк, имя — как удобно)."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.add_pay_sbp))
async def admin_add_pay_sbp_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Строка не должна быть пустой.")
        return
    if db.count_payment_sbp() >= MAX_PAYMENT_SBP:
        await message.answer(f"Уже максимум СБП ({MAX_PAYMENT_SBP}).")
        await state.clear()
        return
    if db.add_payment_sbp(text):
        await message.answer("СБП добавлен.")
    else:
        await message.answer("Не удалось добавить.")
    await state.clear()


@dp.callback_query(F.data == "admin:list_pay_cards")
async def admin_list_pay_cards(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    cards = db.list_payment_cards()
    if not cards:
        await call.message.answer("Список карт пуст.")
        await call.answer()
        return
    lines = [f"[{c['id']}] {c['details']}" for c in cards]
    await call.message.answer(
        "Карты (нажмите, чтобы удалить):\n" + "\n".join(lines),
        reply_markup=keyboards.admin_delete_cards_keyboard(cards),
    )
    await call.answer()


@dp.callback_query(F.data == "admin:list_pay_sbp")
async def admin_list_pay_sbp(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    items = db.list_payment_sbp()
    if not items:
        await call.message.answer("Список СБП пуст.")
        await call.answer()
        return
    lines = [f"[{s['id']}] {s['details']}" for s in items]
    await call.message.answer(
        "СБП (нажмите, чтобы удалить):\n" + "\n".join(lines),
        reply_markup=keyboards.admin_delete_sbp_keyboard(items),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("pcrm:"))
async def admin_remove_card(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    card_id = int(call.data.split(":")[1])
    if db.delete_payment_card(card_id):
        await call.answer("Карта удалена")
    else:
        await call.answer("Не найдено", show_alert=True)


@dp.callback_query(F.data.startswith("psrm:"))
async def admin_remove_sbp(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    sbp_id = int(call.data.split(":")[1])
    if db.delete_payment_sbp(sbp_id):
        await call.answer("СБП удалён")
    else:
        await call.answer("Не найдено", show_alert=True)


def _callback_send_target(call: CallbackQuery) -> tuple[int, int | None]:
    """chat_id и message_thread_id (только для форумов, >0)."""
    chat_id = call.message.chat.id
    tid = getattr(call.message, "message_thread_id", None)
    thread_id = int(tid) if tid is not None and int(tid) > 0 else None
    return chat_id, thread_id


async def _send_admin_text(
    call: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    chat_id, thread_id = _callback_send_target(call)
    kw: dict = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        kw["reply_markup"] = reply_markup
    if thread_id is not None:
        kw["message_thread_id"] = thread_id
    await bot.send_message(**kw)


@dp.callback_query(F.data == "admin:orders_pending")
async def admin_orders_pending(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    rows = db.get_pending_confirm_orders()
    await call.answer()
    if not rows:
        await _send_admin_text(call, "Нет заказов, ожидающих подтверждения.")
        return
    n = len(rows)
    for i, r in enumerate(rows):
        if i > 0:
            await asyncio.sleep(0.4)
        oid = int(r["id"])
        try:
            method_label = _pay_method_label(r.get("pay_method"))
            uname = f"@{r['username']}" if r.get("username") else f"id {r['user_id']}"
            req = r.get("requisite_text") or "—"
            try:
                amt = float(r["amount"])
            except (TypeError, ValueError):
                amt = 0.0
            prefix = f"[{i + 1}/{n}] " if n > 1 else ""
            body = (
                f"{prefix}💳 Заказ #{oid}\n\n"
                f"Покупатель: {uname}\n"
                f"Позиция: {r['product_name']}\n"
                f"Сумма: {amt:.2f} RUB\n"
                f"Способ: {method_label}\n"
                f"Реквизиты:\n{req}"
            )
            if len(body) > 3800:
                body = body[:3797] + "..."
            await _send_admin_text(
                call, body, reply_markup=keyboards.admin_order_actions(oid)
            )
        except Exception as exc:
            logging.exception("Не удалось отправить карточку заказа %s", oid)
            try:
                await _send_admin_text(
                    call,
                    f"Ошибка отправки заказа #{oid}: {exc!s}"[:3500],
                )
            except Exception:
                logging.exception("Не удалось отправить текст ошибки по заказу %s", oid)


@dp.callback_query(F.data == "admin:add_city")
async def admin_add_city_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.add_city_name)
    await call.message.answer("Введите название города:")
    await call.answer()


@dp.message(StateFilter(AdminStates.add_city_name))
async def admin_add_city_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    name = message.text.strip()
    if not name:
        await message.answer("Название не должно быть пустым.")
        return
    if db.add_city(name):
        await message.answer(f"Город '{name}' добавлен.")
    else:
        await message.answer("Такой город уже существует.")
    await state.clear()


@dp.callback_query(F.data == "admin:add_district")
async def admin_add_district_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.add_district_payload)
    await call.message.answer("Введите: city_id|Название района\nПример: 1|Центральный")
    await call.answer()


@dp.message(StateFilter(AdminStates.add_district_payload))
async def admin_add_district_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        city_id_raw, district_name = message.text.split("|", 1)
        city_id = int(city_id_raw.strip())
        district_name = district_name.strip()
    except ValueError:
        await message.answer("Неверный формат. Используйте city_id|Название района")
        return
    if not db.get_city(city_id):
        await message.answer("Город с таким city_id не найден.")
        return
    if not district_name:
        await message.answer("Название района не должно быть пустым.")
        return
    if db.add_district(city_id, district_name):
        await message.answer("Район добавлен.")
    else:
        await message.answer("Такой район уже существует в этом городе.")
    await state.clear()


@dp.callback_query(F.data == "admin:assortment_hub")
async def admin_assortment_hub(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.message.answer(
        "📂 Ассортименты\n\n"
        "Это названия групп на первом шаге каталога.\n"
        "В пользовательском каталоге группа появится, когда будет "
        "хотя бы одна позиция с этим именем ассортимента.\n\n"
        "Добавление позиции по-прежнему можно делать тем же текстом ассортимента "
        "(если строка уже есть здесь — просто совпадёт по имени).",
        reply_markup=keyboards.admin_assortment_hub(),
    )
    await call.answer()


@dp.callback_query(F.data == "admin:add_assortment")
async def admin_add_assortment_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.add_assortment_name)
    await call.message.answer(
        "Введите название ассортимента одной строкой "
        "(без символа | — он зарезервирован для позиций)."
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.add_assortment_name), F.text)
async def admin_add_assortment_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не должно быть пустым.")
        return
    if "|" in name:
        await message.answer("Символ «|» нельзя использовать в названии ассортимента.")
        return
    if db.add_assortment(name):
        await message.answer(
            f"Ассортимент «{name}» добавлен.\n"
            "В каталоге для покупателей он станет виден после первой позиции "
            f"с этим именем в поле «Ассортимент» (➕ Позиция)."
        )
    else:
        await message.answer(
            "Не добавлено: такое имя уже занято (ассортименты не должны совпадать)."
        )
    await state.clear()


@dp.callback_query(F.data == "admin:list_assortments")
async def admin_list_assortments(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    rows = db.list_assortments()
    if not rows:
        await call.message.answer("Ассортиментов ещё нет.")
        await call.answer()
        return
    lines = [
        "Ассортименты [id] — число в скобках у 🗑 = сколько позиций привязано.\n"
        "Удалить можно только строку без позиций (0)."
    ]
    for r in rows:
        lines.append(f"[{r['id']}] {r['name']} — позиций: {int(r.get('product_count') or 0)}")
    await call.message.answer(
        "\n".join(lines),
        reply_markup=keyboards.admin_delete_assortment_keyboard(rows),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("asmrm:"))
async def admin_remove_assortment(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        aid = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer()
        return
    if db.delete_assortment_if_empty(aid):
        await call.answer("Ассортимент удалён")
    else:
        await call.answer(
            "Нельзя удалить: есть позиции с этим ассортиментом. Сначала удалите позиции.",
            show_alert=True,
        )


def _product_titles_for_assortment(assortment_name: str) -> list[str]:
    rows = db.list_product_templates(limit=200)
    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        if str(r.get("assortment_name") or "") != assortment_name:
            continue
        title = str(r.get("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        out.append(title)
    return out


@dp.callback_query(F.data == "admin:add_product")
async def admin_add_product_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    names = db.get_assortment_names()
    await state.set_state(None)
    await state.clear()
    await state.update_data(apw_mode=True)
    await call.message.answer(
        "➕ Добавление позиции (мастер)\n\n"
        "1) Выберите ассортимент из кнопок или введите новый:",
        reply_markup=keyboards.admin_product_wizard_assortment_keyboard(names),
    )
    await call.answer()


@dp.callback_query(F.data == "apw:asm:new")
async def admin_product_wizard_assortment_new(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.add_product_wizard_assortment_input)
    await call.answer()
    await call.message.answer("Введите название нового ассортимента:")


@dp.callback_query(F.data.startswith("apw:asm:"))
async def admin_product_wizard_assortment_pick(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 3 or parts[2] == "new":
        await call.answer()
        return
    try:
        idx = int(parts[2])
    except ValueError:
        await call.answer("Ошибка", show_alert=True)
        return
    names = db.get_assortment_names()
    if idx < 0 or idx >= len(names):
        await call.answer("Ассортимент не найден", show_alert=True)
        return
    assortment = names[idx]
    titles = _product_titles_for_assortment(assortment)
    await state.update_data(apw_assortment=assortment)
    await call.answer()
    await call.message.answer(
        f"2) Ассортимент: {assortment}\n"
        "Выберите позицию из существующих или введите новую:",
        reply_markup=keyboards.admin_product_wizard_title_keyboard(titles),
    )


@dp.message(StateFilter(AdminStates.add_product_wizard_assortment_input), F.text)
async def admin_product_wizard_assortment_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    assortment = (message.text or "").strip()
    if not assortment:
        await message.answer("Ассортимент не должен быть пустым.")
        return
    titles = _product_titles_for_assortment(assortment)
    await state.update_data(apw_assortment=assortment)
    await state.set_state(None)
    await message.answer(
        f"2) Ассортимент: {assortment}\n"
        "Выберите позицию из существующих или введите новую:",
        reply_markup=keyboards.admin_product_wizard_title_keyboard(titles),
    )


@dp.callback_query(F.data == "apw:title:new")
async def admin_product_wizard_title_new(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.add_product_wizard_title_input)
    await call.answer()
    await call.message.answer("Введите название новой позиции:")


@dp.callback_query(F.data.startswith("apw:title:"))
async def admin_product_wizard_title_pick(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 3 or parts[2] == "new":
        await call.answer()
        return
    assortment = str((await state.get_data()).get("apw_assortment") or "")
    titles = _product_titles_for_assortment(assortment)
    try:
        idx = int(parts[2])
    except ValueError:
        await call.answer("Ошибка", show_alert=True)
        return
    if idx < 0 or idx >= len(titles):
        await call.answer("Позиция не найдена", show_alert=True)
        return
    await state.update_data(apw_title=titles[idx])
    await call.answer()
    await call.message.answer(
        f"3) Позиция: {titles[idx]}\nВыберите количество:",
        reply_markup=keyboards.admin_product_wizard_qty_keyboard(),
    )


@dp.message(StateFilter(AdminStates.add_product_wizard_title_input), F.text)
async def admin_product_wizard_title_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название позиции не должно быть пустым.")
        return
    await state.update_data(apw_title=title)
    await state.set_state(None)
    await message.answer(
        f"3) Позиция: {title}\nВыберите количество:",
        reply_markup=keyboards.admin_product_wizard_qty_keyboard(),
    )


@dp.callback_query(F.data == "apw:qty:new")
async def admin_product_wizard_qty_new(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.add_product_wizard_qty_input)
    await call.answer()
    await call.message.answer("Введите количество, например: 1шт, 2шт, 1г, 2.5г")


@dp.callback_query(F.data.startswith("apw:qty:"))
async def admin_product_wizard_qty_pick(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    token = call.data.split(":")[-1]
    mapping = {"1s": (1.0, "шт"), "2s": (2.0, "шт"), "1g": (1.0, "г"), "2g": (2.0, "г")}
    if token == "new":
        await call.answer()
        return
    if token not in mapping:
        await call.answer("Ошибка", show_alert=True)
        return
    qv, qu = mapping[token]
    await state.update_data(apw_qty_value=qv, apw_qty_unit=qu)
    cities = db.get_cities()
    await call.answer()
    await call.message.answer(
        f"4) Кол-во: {qv:g} {qu}\nВыберите город:",
        reply_markup=keyboards.admin_product_wizard_city_keyboard(cities),
    )


@dp.message(StateFilter(AdminStates.add_product_wizard_qty_input), F.text)
async def admin_product_wizard_qty_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    qv, qu = _parse_admin_qty_spec(message.text or "")
    await state.update_data(apw_qty_value=qv, apw_qty_unit=qu)
    await state.set_state(None)
    cities = db.get_cities()
    await message.answer(
        f"4) Кол-во: {qv:g} {qu}\nВыберите город:",
        reply_markup=keyboards.admin_product_wizard_city_keyboard(cities),
    )


@dp.callback_query(F.data.startswith("apw:city:"))
async def admin_product_wizard_city_pick(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        city_id = int(call.data.split(":")[2])
    except (ValueError, IndexError):
        await call.answer("Ошибка", show_alert=True)
        return
    districts = db.get_districts(city_id)
    if not districts:
        await call.answer("В городе нет районов", show_alert=True)
        return
    await call.answer()
    await call.message.answer(
        "5) Выберите район:",
        reply_markup=keyboards.admin_product_wizard_district_keyboard(districts),
    )


@dp.callback_query(F.data.startswith("apw:dist:"))
async def admin_product_wizard_district_pick(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        district_id = int(call.data.split(":")[2])
    except (ValueError, IndexError):
        await call.answer("Ошибка", show_alert=True)
        return
    if not db.get_district(district_id):
        await call.answer("Район не найден", show_alert=True)
        return
    await state.update_data(apw_district_id=district_id)
    await state.set_state(AdminStates.add_product_payload)
    await call.answer()
    await call.message.answer(
        "6) Укажите цену и при желании ссылку:\n"
        "Формат: Цена|[ссылка_авто]\n"
        "Пример: 2500\n"
        "Пример: 2500|https://example.com/x"
    )


@dp.callback_query(F.data == "admin:add_product_last")
async def admin_add_product_last_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    raw = (db.get_setting("last_product_template") or "").strip()
    if not raw:
        await call.answer("Нет сохранённого шаблона позиции.", show_alert=True)
        await call.message.answer(
            "Сначала добавьте хотя бы одну позицию через «➕ Позиция», "
            "потом можно быстро копировать её в другие районы."
        )
        return
    try:
        tpl = json.loads(raw)
        assortment = str(tpl["assortment_name"])
        title = str(tpl["title"])
        qty_value = float(tpl.get("qty_value", 1))
        qty_unit = str(tpl.get("qty_unit", "шт"))
        auto_url = (tpl.get("auto_delivery_url") or "").strip() or None
    except Exception:
        await call.answer("Шаблон повреждён", show_alert=True)
        return
    await state.set_state(AdminStates.add_product_from_last_payload)
    await state.update_data(
        quick_assortment=assortment,
        quick_title=title,
        quick_qty_value=qty_value,
        quick_qty_unit=qty_unit,
        quick_auto_url=auto_url,
    )
    qline = f"{qty_value:g} {qty_unit}"
    await call.message.answer(
        "♻️ Быстрое добавление из последней позиции\n"
        f"Шаблон: [{assortment}] {title} ({qline})\n\n"
        "Отправьте: district_id|Цена|[ссылка_авто]\n"
        "Если ссылку не указать — будет как в шаблоне.\n"
        "Пример: 12|2490\n"
        "Пример с новой ссылкой: 12|2490|https://example.com/x"
    )
    await call.answer()


@dp.callback_query(F.data == "admin:product_templates")
async def admin_product_templates_open(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    rows = db.list_product_templates(limit=40)
    if not rows:
        await call.answer("Шаблонов пока нет", show_alert=True)
        await call.message.answer(
            "Шаблонов пока нет: добавьте позиции в каталог, затем они появятся здесь."
        )
        return
    await call.message.answer(
        "📚 Шаблоны позиций из текущего каталога.\n"
        "Выберите шаблон, затем отправьте district_id|Цена|[ссылка].",
        reply_markup=keyboards.admin_product_templates_keyboard(rows),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("ptpl:"))
async def admin_product_template_pick(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        pid = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer()
        return
    product = db.get_product(pid)
    if not product:
        await call.answer("Шаблон недоступен", show_alert=True)
        return
    assortment = str(product["assortment_name"])
    title = str(product["title"])
    qty_value = float(product.get("qty_value") or 1)
    qty_unit = str(product.get("qty_unit") or "шт")
    auto_url = (product.get("auto_delivery_url") or "").strip() or None
    await state.set_state(AdminStates.add_product_from_last_payload)
    await state.update_data(
        quick_assortment=assortment,
        quick_title=title,
        quick_qty_value=qty_value,
        quick_qty_unit=qty_unit,
        quick_auto_url=auto_url,
    )
    await call.answer()
    await call.message.answer(
        "Шаблон выбран.\n"
        f"[{assortment}] {title} ({qty_value:g} {qty_unit})\n\n"
        "Отправьте: district_id|Цена|[ссылка_авто]"
    )


@dp.message(StateFilter(AdminStates.add_product_payload))
async def admin_add_product_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    assortment_name = str(data.get("apw_assortment") or "").strip()
    title = str(data.get("apw_title") or "").strip()
    district_id = int(data.get("apw_district_id") or 0)
    try:
        qty_val = float(data.get("apw_qty_value") or 1.0)
    except ValueError:
        qty_val = 1.0
    qty_unit = str(data.get("apw_qty_unit") or "шт")
    raw = (message.text or "").strip()
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 1 or not parts[0]:
        await message.answer("Неверный формат. Нужно: Цена|[ссылка_авто]")
        return
    try:
        price = float(parts[0].replace(",", "."))
    except ValueError:
        await message.answer("Неверная цена.")
        return
    auto_url = "|".join(parts[1:]).strip() if len(parts) > 1 else ""
    auto_url = auto_url or (str(data.get("apw_auto_url") or "").strip() or None)
    if not db.get_district(district_id):
        await message.answer("Район с таким district_id не найден.")
        return
    if not assortment_name:
        await message.answer("Название ассортимента не должно быть пустым.")
        return
    if not title:
        await message.answer("Название позиции не должно быть пустым.")
        return
    if price < 0:
        await message.answer("Цена не может быть отрицательной.")
        return
    if db.add_product(
        district_id,
        assortment_name,
        title,
        price,
        auto_url,
        qty_value=qty_val,
        qty_unit=qty_unit,
    ):
        db.set_setting(
            "last_product_template",
            json.dumps(
                {
                    "assortment_name": assortment_name,
                    "title": title,
                    "qty_value": qty_val,
                    "qty_unit": qty_unit,
                    "auto_delivery_url": auto_url or "",
                },
                ensure_ascii=False,
            ),
        )
        extra = " (с автовыдачей)" if auto_url else ""
        q_note = f" {_format_qty_line({'qty_value': qty_val, 'qty_unit': qty_unit})}"
        await message.answer(f"Позиция добавлена{q_note}.{extra}")
    else:
        await message.answer("Не удалось добавить позицию.")
    await state.clear()


@dp.message(StateFilter(AdminStates.add_product_from_last_payload), F.text)
async def admin_add_product_from_last_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    assortment_name = str(data.get("quick_assortment") or "").strip()
    title = str(data.get("quick_title") or "").strip()
    try:
        qty_val = float(data.get("quick_qty_value") or 1)
    except (TypeError, ValueError):
        qty_val = 1.0
    qty_unit = str(data.get("quick_qty_unit") or "шт").strip() or "шт"
    auto_url = (str(data.get("quick_auto_url") or "").strip() or None)
    parts = [p.strip() for p in (message.text or "").strip().split("|")]
    if len(parts) < 2:
        await message.answer("Неверный формат. Нужно: district_id|Цена|[ссылка]")
        return
    try:
        district_id = int(parts[0])
        price = float(parts[1].replace(",", "."))
    except ValueError:
        await message.answer("Неверный district_id или цена.")
        return
    if len(parts) >= 3 and parts[2]:
        auto_url = "|".join(parts[2:]).strip() or None
    if not db.get_district(district_id):
        await message.answer("Район с таким district_id не найден.")
        return
    if price < 0:
        await message.answer("Цена не может быть отрицательной.")
        return
    if db.add_product(
        district_id,
        assortment_name,
        title,
        price,
        auto_url,
        qty_value=qty_val,
        qty_unit=qty_unit,
    ):
        extra = " (с автовыдачей)" if auto_url else ""
        q_note = f" {_format_qty_line({'qty_value': qty_val, 'qty_unit': qty_unit})}"
        await message.answer(
            f"Позиция добавлена из шаблона{q_note}.{extra}\n"
            "Можно отправить ещё district_id|Цена|[ссылка] для следующего района "
            "или /cancel для выхода."
        )
        return
    await message.answer("Не удалось добавить позицию.")


@dp.callback_query(F.data == "admin:update_price")
async def admin_update_price_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.update_price_payload)
    await call.message.answer("Введите: product_id|Новая цена\nПример: 5|2990")
    await call.answer()


@dp.message(StateFilter(AdminStates.update_price_payload))
async def admin_update_price_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        product_id_raw, price_raw = message.text.split("|", 1)
        product_id = int(product_id_raw.strip())
        price = float(price_raw.replace(",", ".").strip())
    except ValueError:
        await message.answer("Неверный формат. Используйте product_id|Новая цена")
        return
    if price < 0:
        await message.answer("Цена не может быть отрицательной.")
        return
    if db.update_product_price(product_id, price):
        await message.answer("Цена обновлена.")
    else:
        await message.answer("Позиция с таким product_id не найдена.")
    await state.clear()


@dp.callback_query(F.data == "admin:delete")
async def admin_delete_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.delete_payload)
    await call.message.answer(
        "Введите что удалить:\n"
        "city|id\n"
        "district|id\n"
        "position|id  (то же, что product|id)\n\n"
        "Пример: position|7"
    )
    await call.answer()


@dp.message(StateFilter(AdminStates.delete_payload))
async def admin_delete_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    try:
        entity, id_raw = message.text.split("|", 1)
        entity = entity.strip().lower()
        entity_id = int(id_raw.strip())
    except ValueError:
        await message.answer("Неверный формат. Используйте entity|id")
        return

    if entity == "city":
        success = db.delete_city(entity_id)
    elif entity == "district":
        success = db.delete_district(entity_id)
    elif entity in ("product", "position"):
        success = db.delete_product(entity_id)
    else:
        await message.answer("Доступные типы: city, district, position (или product)")
        return

    await message.answer("Удалено." if success else "Ничего не удалено (ID не найден).")
    await state.clear()


@dp.callback_query(F.data == "admin:list")
async def admin_list(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    cities = db.get_cities()
    if not cities:
        await call.message.answer("Справочники пусты.")
        await call.answer()
        return

    lines = ["Справочник: город → район → позиции (ассортимент + название позиции):"]
    for city in cities:
        lines.append(f"\nГород [{city['id']}]: {city['name']}")
        districts = db.get_districts(city["id"])
        if not districts:
            lines.append("  - Нет районов")
            continue
        for district in districts:
            lines.append(f"  Район [{district['id']}]: {district['name']}")
            products = db.get_products(district["id"])
            if not products:
                lines.append("    - Нет позиций")
                continue
            for product in products:
                auto = " ⚡авто" if product.get("auto_delivery_url") else ""
                qdisp = _format_qty_line(product)
                lines.append(
                    f"    [{product['id']}] [{product['assortment_name']}] {product['title']} ({qdisp}) — {product['price']:.2f} RUB{auto}"
                )
    full = "\n".join(lines)
    try:
        await _send_admin_plain_chunks(call.message, full)
    except Exception:
        logging.exception("Не удалось отправить admin:list")
        await call.message.answer(
            "Не удалось отправить список целиком. Сократите каталог или повторите позже."
        )
    await call.answer()





async def handle_root(request):
    path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="<h1>SkittlesMarket</h1>", content_type="text/html")


async def handle_info(request):
    uptime = datetime.now() - BOT_START_TIME
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    html = f"""<html><head><title>SMAILO Bot</title></head><body style="font-family:monospace;padding:20px;background:#1a1a1a;color:#0f0;"><pre>
SMAILO BOT - RUNNING
🕐 Uptime: {hours}h {minutes}m {seconds}s</pre></body></html>"""
    return web.Response(text=html, content_type="text/html")


app = web.Application()
app.router.add_get("/", handle_root)
app.router.add_get("/info", handle_info)


@dp.startup()
async def on_startup() -> None:
    global _BOT_USERNAME, _SUPPORT_BOT_USERNAME
    try:
        me = await bot.get_me()
        _BOT_USERNAME = f"@{me.username}" if me and me.username else None
        logging.info("Bot username: %s", _BOT_USERNAME)
    except Exception:
        logging.exception("Failed to get bot username")
    try:
        sme = await support_bot.bot.get_me()
        _SUPPORT_BOT_USERNAME = f"@{sme.username}" if sme and sme.username else None
        logging.info("Support bot username: %s", _SUPPORT_BOT_USERNAME)
    except Exception:
        logging.exception("Failed to get support bot username")


async def run_support_bot_wrapper():
    await support_bot.run_support_bot()


async def webhook_handle(request):
    if request.method != "POST":
        return web.Response(status=405)
    try:
        body = await request.json()
        update = types.Update(**body)
        await dp.feed_update(bot, update)
    except Exception:
        logging.exception("Webhook update error")
    return web.Response(status=200)


async def main() -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", LOCAL_PORT)
    await site.start()
    logging.info(f"Web server started on port {LOCAL_PORT}")

    webhook_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if webhook_url:
        wh = f"{webhook_url.rstrip('/')}/webhook"
        app.router.add_post("/webhook", webhook_handle)
        await bot.set_webhook(wh, allowed_updates=dp.resolve_used_update_types())
        logging.info("Webhook set to %s", wh)
    else:
        logging.info("No RENDER_EXTERNAL_URL — using polling")
        app.router.add_post("/webhook", webhook_handle)

    support_task = asyncio.create_task(run_support_bot_wrapper(), name="support_bot")

    if not webhook_url:
        while True:
            try:
                await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
                return
            except TelegramNetworkError as exc:
                logging.error("Network error while polling Telegram: %s", exc)
                await asyncio.sleep(5)
            finally:
                support_task.cancel()
                try:
                    await support_task
                except asyncio.CancelledError:
                    pass
    else:
        try:
            await support_task
        except Exception:
            logging.exception("Support bot error")


if __name__ == "__main__":
    asyncio.run(main())
