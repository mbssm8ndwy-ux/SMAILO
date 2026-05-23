"""
Очистка каталога и загрузка только городов и районов (Крым).
Позиции и ассортименты добавляются вручную в админке:
  district_id|Ассортимент|Название позиции|Цена

Запуск:
  python scripts/reset_and_seed_catalog.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

import config  # noqa: E402
from db import Database  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("catalog_seed")

# Города Крыма и районы (без товаров — их добавляет админ).
CRIMEA_GEO: list[tuple[str, list[str]]] = [
    ("Симферополь", ["Центральный", "Железнодорожный", "Киевский"]),
    ("Севастополь", ["Ленинский", "Нахимовский", "Гагаринский", "Балаклавский"]),
    ("Керчь", ["Центральный", "Кировский", "Аршинцево"]),
    ("Евпатория", ["Центральный", "Заозёрный"]),
    ("Ялта", ["Ялтинский", "Ливадия", "Гаспра"]),
    ("Феодосия", ["Центральный", "Приморский"]),
    ("Алушта", ["Центральный", "Партенит", "Малый Маяк"]),
    ("Джанкой", ["Центральный", "Северный"]),
    ("Саки", ["Центральный", "Новый"]),
    ("Бахчисарай", ["Центральный", "Бельбекская долина"]),
    ("Судак", ["Центральный", "Новый Свет"]),
    ("Армянск", ["Центральный"]),
    ("Красноперекопск", ["Центральный"]),
    ("Форос", ["Центральный"]),
    ("Инкерман", ["Центральный"]),
]


def _chunks(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


async def send_log_to_telegram(body: str) -> None:
    raw_cid = getattr(config, "LOG_CHAT_ID", None)
    if raw_cid is None or raw_cid == "":
        log.info("LOG_CHAT_ID не задан — сводка только в консоли.")
        return
    try:
        chat_id = int(raw_cid)
    except (TypeError, ValueError):
        log.warning("LOG_CHAT_ID некорректен, пропуск отправки в Telegram.")
        return

    from aiogram import Bot
    from aiogram.client.session.aiohttp import AiohttpSession

    proxy = getattr(config, "PROXY_URL", "") or ""
    session = AiohttpSession(proxy=proxy.strip()) if proxy.strip() else None
    bot = Bot(token=config.API_TOKEN, session=session) if session else Bot(token=config.API_TOKEN)

    topic_raw = getattr(config, "LOG_FORUM_TOPIC_ID", None)
    thread_id = None
    if topic_raw is not None and str(topic_raw).strip() != "":
        try:
            tid = int(topic_raw)
            thread_id = tid if tid > 0 else None
        except (TypeError, ValueError):
            thread_id = None

    header = "📋 Каталог: сброс + города/районы Крыма (без позиций)\n\n"
    parts = _chunks(header + body)
    try:
        for i, part in enumerate(parts):
            if len(parts) > 1:
                part = f"({i + 1}/{len(parts)})\n" + part
            kw: dict = {"chat_id": chat_id, "text": part}
            if thread_id is not None:
                kw["message_thread_id"] = thread_id
            await bot.send_message(**kw)
        log.info("Сводка отправлена в LOG_CHAT_ID=%s", chat_id)
    except Exception:
        log.exception("Не удалось отправить сводку в Telegram")
    finally:
        await bot.session.close()


def seed_crimea_geo(db: Database) -> tuple[int, int, str]:
    """Возвращает (число городов, число районов, текст сводки)."""
    lines: list[str] = []
    n_cities = 0
    n_districts = 0
    for city_name, districts in CRIMEA_GEO:
        if not db.add_city(city_name):
            log.error("Город не добавлен (дубликат?): %s", city_name)
            lines.append(f"ОШИБКА город: {city_name}")
            continue
        n_cities += 1
        log.info("Город: %s", city_name)
        lines.append(f"Город [{city_name}]")
        cities = db.get_cities()
        city = next((c for c in cities if c["name"] == city_name), None)
        if not city:
            continue
        cid = int(city["id"])
        for d_name in districts:
            if db.add_district(cid, d_name):
                n_districts += 1
                log.info("  район: %s", d_name)
                lines.append(f"  район [{d_name}] → district_id будет в «📄 Список»")
            else:
                log.error("Район не добавлен: %s / %s", city_name, d_name)
                lines.append(f"  ОШИБКА район: {d_name}")
    lines.append("")
    lines.append("Позиции не загружены. В админке: district_id|Ассортимент|Позиция|Цена|[1шт или 1г]|ссылка")
    return n_cities, n_districts, "\n".join(lines)


async def async_main() -> None:
    db = Database()
    log.info("Сброс каталога (shop.db в %s)", ROOT)

    stats = db.reset_catalog(clear_orders=True)
    log.info(
        "Удалено: заказы=%s, товары=%s, ассортименты=%s, районы=%s, города=%s",
        stats.get("orders_deleted", 0),
        stats.get("products_deleted", 0),
        stats.get("assortments_deleted", 0),
        stats.get("districts_deleted", 0),
        stats.get("cities_deleted", 0),
    )

    nc, nd, detail = seed_crimea_geo(db)
    log.info("Добавлено городов: %s, районов: %s", nc, nd)

    summary = (
        f"Удалено:\n"
        f"  заказов: {stats.get('orders_deleted', 0)}\n"
        f"  позиций (товаров): {stats.get('products_deleted', 0)}\n"
        f"  строк ассортиментов: {stats.get('assortments_deleted', 0)}\n"
        f"  районов: {stats.get('districts_deleted', 0)}\n"
        f"  городов: {stats.get('cities_deleted', 0)}\n\n"
        f"Загружено городов: {nc}, районов: {nd}\n\n"
        f"{detail}"
    )
    await send_log_to_telegram(summary)
    try:
        print(summary)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((summary + "\n").encode("utf-8", errors="replace"))


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
