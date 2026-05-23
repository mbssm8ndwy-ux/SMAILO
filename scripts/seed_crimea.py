import sqlite3
import sys
from datetime import datetime, timedelta
import random

DB_PATH = "shop.db"
BACKUP_FILE = "shop_reviews_backup.sql"

REVIEWS_CRIMEA = [
    "Удачная находка доволен",
    "Качевство радует",
    "Все отлично спасибо",
    "Приятно иметь дело спасибо",
    "маганиты снимаются оч быстро спасибо",
    "качевство пушка ! будем брать еще",
    "Спасибо товар приехал быстро",
    "Быстро качественно спасибо",
    "Все супер приятно иметь дело",
    "спасибо брат все огонь",
    "Класный товар доволен",
    "Молодцы работаете хорошо спасибо",
    "качевво на высоте спасибо",
    "Маганиты просто огонь быстро снял",
    "Все ок спасибо магазину",
    "Хорошая находка ребята спасибо",
    "Дома все четко спасибо",
    "Быстро делаете свое дело спасибо",
    "качество хорошое спасибо",
    "Приятно работать спасибо",
    "Все на уровне спасибо",
    "Молодцы ребята так держать",
    "От души спасибо братья",
    "хороший продукт спасибо",
    "доволен покупкой спасибо",
    "Топ за свои деньги спасибо",
    "быстро снял маганиты спасибо",
    "Качевство радует будем брать",
    "спасибо за работу все понравилось",
    "респект магазину спасибо",
    "Отличная находка спасибо",
    "Хороший товар быстрая подача спасибо",
    "качевво топ спасибо)",
    "Все приехало целое спасибо",
    "Доволен сервисом спасибо",
    "Спасибо за качевство и скорость",
    "Все шикарно спасибо ребята",
    "Достойно спасибо",
    "Лучшие в городе спасибо",
    "качевво огонь будем брать еще",
    "Все хорошо спасибо",
    "Спасибо быстро и качественно",
    "спасибо за работу ребят",
    "класный стафф спасибо",
    "От души спасибо все залетело",
    "Приятно иметь дело хороший магаз",
    "Спасибо все дома",
    "как всегда качевво топ спасибо",
    "все четко спасибо",
    "доволен удачная находка",
    "Все на месте спасибо большое",
    "Работаете отлично спасибо",
    "доволен как слон спасибо",
    "Классный сервис спасибо",
]

# Products to add to Sevastopol districts
SEVA_PRODUCTS = {
    "Балаклава": [
        ("Lemon Haze", 4100.0, 1.0, "шт"),
        ("Afgan Cush VHQ", 2455.0, 1.0, "шт"),
    ],
    "Гагаринский (ПОР / Омега)": [
        ("Bruce Banner", 4970.0, 1.0, "шт"),
        ("Lemon Haze", 2100.0, 1.0, "шт"),
    ],
    "Инкерман": [
        ("Bruce Banner касание", 2770.0, 1.0, "шт"),
        ("Lemon Haze", 4155.0, 1.0, "шт"),
    ],
    "Центр (Ленина / Нахимова)": [
        ("Lemon Haze", 6000.0, 1.0, "шт"),
        ("Afgan Cush VHQ", 4140.0, 1.0, "шт"),
    ],
    "Северная сторона": [
        ("Lizard King", 6000.0, 1.0, "шт", "Грибы"),
    ],
}

def _backup_value(v):
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)

def backup():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT id, review_text, review_rating, review_at, status
        FROM orders
    """).fetchall()
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        for r in rows:
            oid = r[0]
            txt = _backup_value(r[1])
            rating = _backup_value(r[2])
            rat = _backup_value(r[3])
            status = _backup_value(r[4])
            f.write(f"UPDATE orders SET review_text={txt}, review_rating={rating}, review_at={rat}, status={status} WHERE id={oid};\n")
    conn.close()
    print(f"Backup -> {BACKUP_FILE}")

def restore():
    conn = sqlite3.connect(DB_PATH)
    with open(BACKUP_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                conn.execute(line)
    conn.commit()
    conn.close()
    print("Restored from backup OK")

def add_sevastopol_products():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    seva_city_id = 26
    districts = conn.execute(
        "SELECT id, name FROM districts WHERE city_id = ?", (seva_city_id,)
    ).fetchall()
    district_map = {r["name"]: r["id"] for r in districts}

    # Get assortment IDs
    ass_map = {}
    for row in conn.execute("SELECT id, name FROM assortments").fetchall():
        ass_map[row["name"]] = row["id"]
    # Default assortment
    default_ass = "Гидропоника"

    added = 0
    for dname, products in SEVA_PRODUCTS.items():
        did = district_map.get(dname)
        if not did:
            print(f"  District '{dname}' not found, skipping")
            continue
        for prod in products:
            title = prod[0]
            price = prod[1]
            qty_val = prod[2] if len(prod) > 2 else 1.0
            qty_unit = prod[3] if len(prod) > 3 else "шт"
            ass_name = prod[4] if len(prod) > 4 else default_ass
            aid = ass_map.get(ass_name)
            if not aid:
                print(f"  Assortment '{ass_name}' not found, creating")
                conn.execute("INSERT INTO assortments(name) VALUES (?)", (ass_name,))
                aid = conn.execute("SELECT id FROM assortments WHERE name = ?", (ass_name,)).fetchone()[0]
                ass_map[ass_name] = aid

            # Check if product already exists
            exists = conn.execute(
                "SELECT id FROM products WHERE district_id=? AND assortment_id=? AND title=?",
                (did, aid, title)
            ).fetchone()
            if not exists:
                conn.execute("""
                    INSERT INTO products(district_id, assortment_id, title, price, qty_value, qty_unit)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (did, aid, title, price, qty_val, qty_unit))
                added += 1
                print(f"  Added: {dname} -> {title} ({price}rub, {ass_name})")

    conn.commit()
    conn.close()
    print(f"Added {added} products to Sevastopol")
    return added > 0

def seed():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Clean previously seeded reviews (id > 27)
    conn.execute("DELETE FROM orders WHERE id > 27 AND review_text IS NOT NULL")
    conn.execute("UPDATE orders SET review_text=NULL, review_rating=NULL, review_at=NULL WHERE id > 27")
    conn.commit()

    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_end = now

    target_cities = {"Симферополь": 22, "Ялта": 23, "Севастополь": 26}
    city_names_rev = {v: k for k, v in target_cities.items()}

    # Get products for these cities
    products = conn.execute("""
        SELECT p.id, p.title, p.price, p.qty_value, p.qty_unit,
               a.name as assortment, c.name as city, d.name as district
        FROM products p
        JOIN assortments a ON a.id = p.assortment_id
        JOIN districts d ON d.id = p.district_id
        JOIN cities c ON c.id = d.city_id
        WHERE c.id IN (22, 23, 26)
        ORDER BY c.name
    """).fetchall()

    product_list = [dict(r) for r in products]
    if not product_list:
        print("No products found for the target cities!")
        return

    # Group by city
    by_city = {}
    for p in product_list:
        by_city.setdefault(p["city"], []).append(p)

    print(f"Products available:")
    for city, prods in by_city.items():
        print(f"  {city}: {len(prods)} products")

    users = conn.execute("SELECT user_id FROM bot_users").fetchall()
    user_ids = [r[0] for r in users] if users else [7998704133, 7817234911, 1590286397]
    pay_methods = ["card", "sbp", "usdt", "balance"]

    # Target: ~8-10 reviews per city for this month
    targets = {
        "Симферополь": random.randint(8, 10),
        "Ялта": random.randint(7, 9),
        "Севастополь": random.randint(6, 8),
    }

    created = 0
    for city_name, target in targets.items():
        city_products = by_city.get(city_name, [])
        if not city_products:
            print(f"  No products for {city_name}, skipping")
            continue

        # Check existing completed for this month
        city_id = target_cities[city_name]
        existing = conn.execute("""
            SELECT COUNT(*) FROM orders o
            JOIN products p ON p.id = o.product_id
            JOIN districts d ON d.id = p.district_id
            WHERE o.status = 'completed'
              AND d.city_id = ?
              AND o.created_at >= ?
        """, (city_id, month_start.strftime("%Y-%m-%d"))).fetchone()[0]

        needed = max(0, target - existing)
        if needed == 0:
            print(f"  {city_name}: already has {existing}, skipping")
            continue

        print(f"  {city_name}: need {needed} more reviews (existing: {existing})")

        for i in range(needed):
            product = random.choice(city_products)
            user_id = random.choice(user_ids)
            pay_method = random.choice(pay_methods)
            amount = product["price"]

            days_offset = random.randint(0, max(0, (today_end - month_start).days - 1))
            order_date = month_start + timedelta(
                days=days_offset,
                hours=random.randint(10, 22),
                minutes=random.randint(0, 59)
            )
            review_date = order_date + timedelta(minutes=random.randint(5, 180))

            review_text = random.choice(REVIEWS_CRIMEA)
            rating = random.choices([5, 4], weights=[80, 20])[0]
            requisite_text = f"Оплата {pay_method} ({amount:.2f} РУБ)"

            conn.execute("""
                INSERT INTO orders
                (user_id, username, chat_id, product_id, pay_method, requisite_id, requisite_text, amount, status, review_text, review_rating, review_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)
            """, (
                user_id, None, user_id, product["id"], pay_method,
                None, requisite_text, amount,
                review_text, rating,
                review_date.strftime("%Y-%m-%d %H:%M:%S"),
                order_date.strftime("%Y-%m-%d %H:%M:%S"),
            ))
            created += 1

    conn.commit()
    conn.close()
    print(f"\nCreated {created} new reviews this month!")
    print("Restore: python scripts/seed_crimea.py restore")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "restore":
            restore()
        elif cmd == "backup":
            backup()
        else:
            print("Usage: python scripts/seed_crimea.py [backup|restore]")
    else:
        backup()
        restore()
        # Clean orphan orders from previous seeds
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM orders WHERE id > 27 AND (review_text IS NULL OR TRIM(COALESCE(review_text, '')) = '')")
        conn.commit()
        conn.close()
        add_sevastopol_products()
        seed()
