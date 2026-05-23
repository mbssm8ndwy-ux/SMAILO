import sqlite3
import sys
from datetime import datetime, timedelta
import random

DB_PATH = "shop.db"
BACKUP_FILE = "shop_reviews_backup.sql"

REVIEWS_LIKE_USER_WANTS = [
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
]


def _backup_value(v):
    if v is None:
        return "NULL"
    if isinstance(v, str):
        escaped = v.replace("'", "''")
        return f"'{escaped}'"
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
    print(f"Backup saved -> {BACKUP_FILE}")
    return True


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


def seed():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Clear all generated reviews (only ones we added in previous run)
    # We identify "our" reviews as those where status=completed AND
    # the review_text exists AND the order was NOT in the backup file.
    # Simpler: just clear all non-backed-up reviews first.
    # Actually, let's just restore first to be clean.
    conn.executescript("""
        UPDATE orders SET review_text=NULL, review_rating=NULL, review_at=NULL
        WHERE review_text IS NOT NULL AND id > 27
    """)
    conn.commit()
    conn.close()
    # Now re-open and seed fresh
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    products = conn.execute("""
        SELECT p.id, p.title, p.price, a.name as assortment
        FROM products p
        LEFT JOIN assortments a ON a.id = p.assortment_id
    """).fetchall()
    product_list = [dict(r) for r in products]

    users = conn.execute("SELECT user_id FROM bot_users").fetchall()
    user_ids = [r[0] for r in users] if users else [7998704133, 7817234911, 1590286397]

    pay_methods = ["card", "sbp", "usdt", "balance"]
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Previous month (April)
    prev_month_start = today.replace(month=now.month - 1, day=1)
    if prev_month_start.month == 12:
        prev_month_end = prev_month_start.replace(year=prev_month_start.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        prev_month_end = prev_month_start.replace(month=prev_month_start.month + 1, day=1) - timedelta(days=1)
    prev_month_end = prev_month_end.replace(hour=23, minute=59, second=59)

    existing_april = conn.execute("""
        SELECT COUNT(*) FROM orders
        WHERE status='completed' AND created_at >= ? AND created_at <= ?
    """, (prev_month_start.strftime("%Y-%m-%d"), prev_month_end.strftime("%Y-%m-%d"))).fetchone()[0]

    existing_may = conn.execute("""
        SELECT COUNT(*) FROM orders
        WHERE status='completed' AND created_at >= ?
    """, (today.replace(day=1).strftime("%Y-%m-%d"),)).fetchone()[0]

    print(f"Existing completed: April={existing_april}, May={existing_may}")

    new_april = max(0, 8 - existing_april)
    new_may = max(0, 12 - existing_may)

    print(f"Adding: April={new_april}, May={new_may}")

    created = 0
    for month_name, count, start_d, end_d in [
        ("April", new_april, prev_month_start, prev_month_end),
        ("May", new_may, today.replace(day=1), now),
    ]:
        for i in range(count):
            product = random.choice(product_list)
            user_id = random.choice(user_ids)
            pay_method = random.choice(pay_methods)
            amount = product["price"]

            offset_days = random.randint(0, max(0, (end_d - start_d).days - 1))
            order_date = start_d + timedelta(days=offset_days, hours=random.randint(10, 22), minutes=random.randint(0, 59))
            review_date = order_date + timedelta(minutes=random.randint(5, 120))

            review_text = random.choice(REVIEWS_LIKE_USER_WANTS)
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
    print(f"Created {created} new reviews!")
    print("Rollback: python scripts/seed_reviews.py restore")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "backup":
            backup()
        elif cmd == "restore":
            restore()
        else:
            print("Usage: python scripts/seed_reviews.py [backup|restore]")
    else:
        backup()
        seed()
