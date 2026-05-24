from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CATALOG_NAMES_PER_PAGE = 20
BTN_BACK = "« Вернуться назад"


def captcha_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🤖 Я не бот", callback_data="captcha:verify")
    return kb.as_markup()


def main_menu(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🛒 Каталог")],
        [KeyboardButton(text="👤 Мой кабинет")],
        [
            KeyboardButton(text="📞 Поддержка"),
            KeyboardButton(text="⭐ Отзывы"),
        ],
        [KeyboardButton(text="💳 Поддержка по платежам")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="⚙️ Админка")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def catalog_assortment_keyboard(names: list, page: int):
    n = len(names)
    total_pages = max(1, (n + CATALOG_NAMES_PER_PAGE - 1) // CATALOG_NAMES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * CATALOG_NAMES_PER_PAGE
    chunk = names[start : start + CATALOG_NAMES_PER_PAGE]
    kb = InlineKeyboardBuilder()
    for i, name in enumerate(chunk):
        gidx = start + i
        label = name if len(name) <= 42 else name[:39] + "..."
        kb.button(text=label, callback_data=f"nav:s:{gidx}")
    kb.adjust(1)
    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="« Стр.", callback_data=f"nav:pg:{page - 1}")
        )
    nav_row.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="nav:z")
    )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(text="Стр. »", callback_data=f"nav:pg:{page + 1}")
        )
    if nav_row:
        kb.row(*nav_row)
    kb.row(InlineKeyboardButton(text="💼 Работа", callback_data="nav:work"))
    kb.row(InlineKeyboardButton(text=BTN_BACK, callback_data="nav:x"))
    return kb.as_markup()


def catalog_cities_keyboard(name_idx: int, page: int, cities):
    kb = InlineKeyboardBuilder()
    for c in cities:
        kb.button(
            text=c["name"],
            callback_data=f"nav:g:{name_idx}:{c['id']}:{page}",
        )
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text=BTN_BACK, callback_data=f"nav:b0:{page}"))
    return kb.as_markup()


def catalog_districts_keyboard(name_idx: int, city_id: int, page: int, districts):
    kb = InlineKeyboardBuilder()
    for d in districts:
        kb.button(
            text=d["name"],
            callback_data=f"nav:r:{name_idx}:{city_id}:{d['id']}",
        )
    kb.adjust(2)
    kb.row(
        InlineKeyboardButton(
            text=BTN_BACK, callback_data=f"nav:b1:{name_idx}:{page}"
        )
    )
    return kb.as_markup()


def _button_qty(p: dict) -> str:
    try:
        v = float(p.get("qty_value") if p.get("qty_value") is not None else 1)
    except (TypeError, ValueError):
        v = 1.0
    u = (p.get("qty_unit") or "шт").strip().lower()
    sym = "г" if u in ("г", "g") else "шт"
    return f"{v:g} {sym}"


def catalog_positions_keyboard(
    name_idx: int, city_id: int, district_id: int, page: int, products: list
):
    kb = InlineKeyboardBuilder()
    for p in products:
        prefix = "⚡ " if p.get("auto_delivery_url") else ""
        label = f"{prefix}{p['title']} ({_button_qty(p)}) — {p['price']:.2f} ₽"
        if len(label) > 64:
            label = label[:61] + "..."
        kb.button(text=label, callback_data=f"nav:p:{p['id']}")
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(
            text=BTN_BACK,
            callback_data=f"nav:b2:{name_idx}:{city_id}:{page}",
        )
    )
    return kb.as_markup()


CABINET_TOPUP_AMOUNTS = (500, 1000, 2000, 5000)


def cabinet_main_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Пополнить", callback_data="cab:topup")
    kb.button(text="🌶️ Реферальная система", callback_data="cab:ref")
    kb.button(text="📦 Мои заказы", callback_data="cab:orders")
    kb.button(text="💰 История баланса", callback_data="cab:hist")
    kb.adjust(1)
    return kb.as_markup()


def cabinet_topup_amounts_inline():
    kb = InlineKeyboardBuilder()
    for amt in CABINET_TOPUP_AMOUNTS:
        kb.button(text=f"💵 {amt} ₽", callback_data=f"cab:tamt:{amt}")
    kb.row(InlineKeyboardButton(text="🔙 В кабинет", callback_data="cab:open"))
    kb.adjust(1)
    return kb.as_markup()


def cabinet_topup_methods_inline(amount: int, flags: dict):
    """flags: card, sbp, usdt, btc — какие кнопки показывать."""
    kb = InlineKeyboardBuilder()
    if flags.get("card"):
        kb.button(text="💳 Карта", callback_data=f"cab:tmeth:{amount}:card")
    if flags.get("sbp"):
        kb.button(text="💠 СБП", callback_data=f"cab:tmeth:{amount}:sbp")
    if flags.get("usdt"):
        kb.button(text="💚 USDT TRC20", callback_data=f"cab:tmeth:{amount}:usdt")
    if flags.get("btc"):
        kb.button(text="🟠 Bitcoin (BTC)", callback_data=f"cab:tmeth:{amount}:btc")
    kb.row(InlineKeyboardButton(text="🔙 К суммам", callback_data="cab:topup"))
    kb.adjust(1)
    return kb.as_markup()


def cabinet_topup_paid_inline(request_id: int, amount: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Я оплатил", callback_data=f"cab:tclaim:{request_id}")
    kb.row(
        InlineKeyboardButton(
            text="🔙 Другой способ", callback_data=f"cab:tamt:{amount}"
        )
    )
    kb.row(InlineKeyboardButton(text="🔙 К суммам", callback_data="cab:topup"))
    kb.adjust(1)
    return kb.as_markup()


def cabinet_back_to_menu_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 В кабинет", callback_data="cab:open")
    kb.adjust(1)
    return kb.as_markup()


def support_root_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="❓ Вопрос", callback_data="sup:q")
    kb.button(text="⚠️ Проблема", callback_data="sup:p")
    kb.adjust(1)
    return kb.as_markup()


def support_problem_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="📦 С заказом", callback_data="sup:po")
    kb.button(text="💵 С пополнением", callback_data="sup:pt")
    kb.adjust(1)
    return kb.as_markup()


def order_review_invite_markup(order_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="⭐ Оставить отзыв", callback_data=f"revgo:{order_id}")
    kb.adjust(1)
    return kb.as_markup()


def order_review_rating_markup(order_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="⭐ 1", callback_data=f"revr:{order_id}:1")
    kb.button(text="⭐ 2", callback_data=f"revr:{order_id}:2")
    kb.button(text="⭐ 3", callback_data=f"revr:{order_id}:3")
    kb.button(text="⭐ 4", callback_data=f"revr:{order_id}:4")
    kb.button(text="⭐ 5", callback_data=f"revr:{order_id}:5")
    kb.adjust(5)
    return kb.as_markup()


def cabinet_orders_markup(orders: list):
    """Завершённые заказы без отзыва — кнопки; всегда строка «В кабинет»."""
    kb = InlineKeyboardBuilder()
    n = 0
    for o in orders:
        if o.get("status") != "completed":
            continue
        if (o.get("review_text") or "").strip():
            continue
        oid = int(o["id"])
        kb.button(text=f"⭐ Отзыв №{oid}", callback_data=f"revgo:{oid}")
        n += 1
        if n >= 12:
            break
    if n:
        kb.adjust(2)
    kb.row(InlineKeyboardButton(text="🔙 В кабинет", callback_data="cab:open"))
    return kb.as_markup()


def admin_topup_decide_markup(request_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Зачислить", callback_data=f"atu_ok:{request_id}")
    kb.button(text="❌ Отклонить", callback_data=f"atu_no:{request_id}")
    kb.adjust(2)
    return kb.as_markup()


def settings_hub():
    kb = InlineKeyboardBuilder()
    kb.button(text="👋 Текст /start", callback_data="admin:set_welcome_text")
    kb.button(text="🖼 Фото /start", callback_data="admin:set_welcome_photo")
    kb.button(text="🗑 Убрать фото /start", callback_data="admin:clear_welcome_photo")
    kb.button(text="☎️ Поддержка (кнопка меню)", callback_data="admin:set_support")
    kb.button(text="⭐ Отзывы (кнопка меню)", callback_data="admin:set_reviews")
    kb.button(
        text="💳 Платежи (кнопка меню)",
        callback_data="admin:set_pay_support",
    )
    kb.button(text="👤 О нас — резерв", callback_data="admin:set_about")
    kb.button(text="📜 Правила — резерв", callback_data="admin:set_rules")
    kb.adjust(1)
    return kb.as_markup()


def admin_assortment_hub():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить ассортимент", callback_data="admin:add_assortment")
    kb.button(text="📋 Список / удалить пустой", callback_data="admin:list_assortments")
    kb.adjust(1)
    return kb.as_markup()


def admin_delete_assortment_keyboard(rows: list):
    kb = InlineKeyboardBuilder()
    for r in rows:
        short = r["name"] if len(r["name"]) <= 20 else r["name"][:17] + "..."
        cnt = int(r.get("product_count") or 0)
        suffix = "" if cnt == 0 else f"·{cnt}"
        label = f"🗑{r['id']}{suffix} {short}"
        if len(label) > 64:
            label = label[:61] + "..."
        kb.button(text=label, callback_data=f"asmrm:{r['id']}")
    kb.adjust(1)
    return kb.as_markup()


def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🛠 Настройки", callback_data="admin:settings_hub")
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(
            text="📂 Ассортименты (каталог)",
            callback_data="admin:assortment_hub",
        ),
    )
    kb.button(text="➕ Город", callback_data="admin:add_city")
    kb.button(text="➕ Район", callback_data="admin:add_district")
    kb.button(text="➕ Позиция", callback_data="admin:add_product")
    kb.button(text="♻️ Позиция (последняя)", callback_data="admin:add_product_last")
    kb.button(text="📚 Шаблоны позиций", callback_data="admin:product_templates")
    kb.button(text="💸 Цена", callback_data="admin:update_price")
    kb.button(text="📦 Подтверждения оплат", callback_data="admin:orders_pending")
    kb.button(text="💰 Оплата", callback_data="admin:payments_hub")
    kb.button(text="🗑 Удалить", callback_data="admin:delete")
    kb.button(text="📄 Список", callback_data="admin:list")
    kb.button(text="📣 Рассылка", callback_data="admin:broadcast")
    kb.button(text="👥 База и рефералы", callback_data="admin:userbase")
    kb.button(text="🎫 Промокоды", callback_data="admin:promos")
    kb.adjust(2)
    return kb.as_markup()


def admin_product_templates_keyboard(rows: list):
    kb = InlineKeyboardBuilder()
    for r in rows:
        q = f"{float(r.get('qty_value') or 1):g} {r.get('qty_unit') or 'шт'}"
        title = f"[{r['assortment_name']}] {r['title']} ({q})"
        if len(title) > 56:
            title = title[:53] + "..."
        kb.button(
            text=title,
            callback_data=f"ptpl:{int(r['template_product_id'])}",
        )
    kb.adjust(1)
    return kb.as_markup()


def admin_product_wizard_assortment_keyboard(names: list):
    kb = InlineKeyboardBuilder()
    for i, name in enumerate(names[:40]):
        short = name if len(name) <= 38 else name[:35] + "..."
        kb.button(text=short, callback_data=f"apw:asm:{i}")
    kb.button(text="✏️ Ввести новый ассортимент", callback_data="apw:asm:new")
    kb.adjust(1)
    return kb.as_markup()


def admin_product_wizard_title_keyboard(titles: list):
    kb = InlineKeyboardBuilder()
    for i, title in enumerate(titles[:40]):
        short = title if len(title) <= 38 else title[:35] + "..."
        kb.button(text=short, callback_data=f"apw:title:{i}")
    kb.button(text="✏️ Ввести новое название позиции", callback_data="apw:title:new")
    kb.adjust(1)
    return kb.as_markup()


def admin_product_wizard_qty_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="1 шт", callback_data="apw:qty:1s")
    kb.button(text="2 шт", callback_data="apw:qty:2s")
    kb.button(text="1 г", callback_data="apw:qty:1g")
    kb.button(text="2 г", callback_data="apw:qty:2g")
    kb.button(text="✏️ Ввести вручную", callback_data="apw:qty:new")
    kb.adjust(2)
    return kb.as_markup()


def admin_product_wizard_city_keyboard(cities: list):
    kb = InlineKeyboardBuilder()
    for c in cities[:60]:
        kb.button(text=c["name"], callback_data=f"apw:city:{c['id']}")
    kb.adjust(2)
    return kb.as_markup()


def admin_product_wizard_district_keyboard(districts: list):
    kb = InlineKeyboardBuilder()
    for d in districts[:80]:
        kb.button(text=d["name"], callback_data=f"apw:dist:{d['id']}")
    kb.adjust(2)
    return kb.as_markup()


def payments_hub():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Карта", callback_data="admin:add_pay_card")
    kb.button(text="📋 Карты", callback_data="admin:list_pay_cards")
    kb.button(text="➕ СБП", callback_data="admin:add_pay_sbp")
    kb.button(text="📋 СБП", callback_data="admin:list_pay_sbp")
    kb.button(text="💚 Адрес USDT TRC20", callback_data="admin:set_crypto_usdt")
    kb.button(text="🟠 Адрес BTC", callback_data="admin:set_crypto_btc")
    kb.button(text="💵 Пополнения баланса", callback_data="admin:topups_pending")
    kb.button(text="👛 Балансы пользователей", callback_data="admin:balance_hub")
    kb.button(text="📦 Заказы (ждут)", callback_data="admin:orders_pending")
    kb.adjust(2)
    return kb.as_markup()


def admin_promos_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать промокод", callback_data="admin:promo_add")
    kb.button(text="📋 Список промокодов", callback_data="admin:promo_list")
    kb.adjust(1)
    return kb.as_markup()


def admin_promo_list_keyboard(promos: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in promos:
        status = "✅" if p["is_active"] else "❌"
        used = f"{p['current_uses']}/{p['max_uses']}" if p['max_uses'] > 0 else f"{p['current_uses']}/∞"
        label = f"{status} {p['code']} -{p['discount_percent']}% ({used})"
        kb.button(text=label, callback_data=f"admin:promo_toggle:{p['id']}")
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin:promos"))
    kb.adjust(1)
    return kb.as_markup()


def broadcast_confirm_markup():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Разослать всем", callback_data="bcst:go")
    kb.button(text="❌ Отмена", callback_data="bcst:x")
    kb.adjust(2)
    return kb.as_markup()


def admin_balance_hub():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Зачислить на баланс", callback_data="admin:bal_credit")
    kb.button(text="➖ Списать с баланса", callback_data="admin:bal_debit")
    kb.button(text="🔍 Узнать баланс", callback_data="admin:bal_lookup")
    kb.adjust(1)
    return kb.as_markup()


_WORK_DEPOSIT_AMOUNT = 5000


def work_options_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"💳 Залог {_WORK_DEPOSIT_AMOUNT}₽", callback_data="work:deposit")
    kb.button(text="📞 Связаться с оператором", callback_data="work:operator")
    kb.adjust(1)
    return kb.as_markup()


def work_pay_method_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Карта", callback_data="work:meth:card")
    kb.button(text="💠 СБП", callback_data="work:meth:sbp")
    kb.button(text="💚 USDT TRC20", callback_data="work:meth:usdt")
    kb.button(text="🟠 Bitcoin (BTC)", callback_data="work:meth:btc")
    kb.adjust(1)
    return kb.as_markup()


def work_paid_keyboard(request_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Я оплатил", callback_data=f"work:claim:{request_id}")
    kb.adjust(1)
    return kb.as_markup()


def promo_ask_keyboard(product_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🎫 Ввести промокод", callback_data=f"promo:enter:{product_id}")
    kb.button(text="➡️ Продолжить без промо", callback_data=f"promo:skip:{product_id}")
    kb.adjust(1)
    return kb.as_markup()


def pay_method_keyboard(
    product_id: int,
    *,
    show_balance: bool = False,
    show_usdt: bool = False,
    show_btc: bool = False,
):
    kb = InlineKeyboardBuilder()
    if show_balance:
        kb.button(text="💰 С баланса", callback_data=f"paym:balance:{product_id}")
    kb.button(text="💳 Карта", callback_data=f"paym:card:{product_id}")
    kb.button(text="💠 СБП", callback_data=f"paym:sbp:{product_id}")
    if show_usdt:
        kb.button(text="💚 USDT TRC20", callback_data=f"paym:usdt:{product_id}")
    if show_btc:
        kb.button(text="🟠 Bitcoin (BTC)", callback_data=f"paym:btc:{product_id}")
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(
            text=BTN_BACK, callback_data=f"bprd:{product_id}"
        ),
    )
    return kb.as_markup()


def order_paid_keyboard(order_id: int, product_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Я оплатил", callback_data=f"opaid:{order_id}")
    kb.row(
        InlineKeyboardButton(
            text=BTN_BACK, callback_data=f"bprd:{product_id}"
        ),
    )
    return kb.as_markup()


def admin_order_actions(order_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Выдать", callback_data=f"ocf:{order_id}")
    kb.button(text="❌ Отменить", callback_data=f"ocr:{order_id}")
    kb.adjust(2)
    return kb.as_markup()


def delivery_confirm_keyboard():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Выдать покупателю", callback_data="dlv_ok")
    kb.button(text="✏️ Изменить текст", callback_data="dlv_edit")
    kb.button(text="❌ Отмена", callback_data="dlv_cancel")
    kb.adjust(1)
    return kb.as_markup()


def admin_delete_cards_keyboard(cards):
    kb = InlineKeyboardBuilder()
    for c in cards:
        short = c["details"] if len(c["details"]) <= 28 else c["details"][:25] + "..."
        kb.button(text=f"🗑 [{c['id']}] {short}", callback_data=f"pcrm:{c['id']}")
    kb.adjust(1)
    return kb.as_markup()


def admin_delete_sbp_keyboard(items):
    kb = InlineKeyboardBuilder()
    for s in items:
        short = s["details"] if len(s["details"]) <= 28 else s["details"][:25] + "..."
        kb.button(text=f"🗑 [{s['id']}] {short}", callback_data=f"psrm:{s['id']}")
    kb.adjust(1)
    return kb.as_markup()
