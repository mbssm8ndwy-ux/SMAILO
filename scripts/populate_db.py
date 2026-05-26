"""Populate shop.db with fake reviews and products across all districts."""
import sqlite3
import random
from datetime import datetime, timedelta

conn = sqlite3.connect("shop.db")
c = conn.cursor()

# ========== 1. FAKE REVIEWS ==========
review_texts = [
    "\u0434\u043e\u043c\u0430 \u0432\u0441\u0435 \u043e\u0442\u043b\u0438\u0447\u043d\u043e",
    "\u043c\u0430\u0433\u043d\u0438\u0442\u044b \u043f\u0443\u0448\u043a\u0430",
    "\u0432 \u043a\u0430\u0441\u0430\u043d\u0438\u0435",
    "\u043b\u0443\u0447\u0448\u0438\u0439 \u0448\u043e\u043f",
    "\u0432\u0441\u0435 \u043e\u0442\u043b\u0438\u0447\u043d\u043e \u043a\u0430\u043a \u0432\u0441\u0435\u0433\u0434\u0430",
    "\u0442\u043e\u0432\u0430\u0440 \u0441\u0443\u043f\u0435\u0440 \u0441\u043f\u0430\u0441\u0438\u0431\u043e",
    "\u0434\u043e\u0441\u0442\u0430\u0432\u043a\u0430 \u0431\u044b\u0441\u0442\u0440\u0430\u044f \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u043e \u0442\u043e\u043f",
    "\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0443\u044e \u0432\u0441\u0435\u043c",
    "\u043e\u0447\u0435\u043d\u044c \u0434\u043e\u0432\u043e\u043b\u0435\u043d \u0437\u0430\u043a\u0430\u0437\u043e\u043c",
    "\u0432\u0437\u044f\u043b \u0434\u0440\u0443\u0437\u044c\u044f\u043c \u0442\u043e\u0436\u0435 \u043f\u043e\u043d\u0440\u0430\u0432\u0438\u043b\u043e\u0441\u044c",
    "\u0441\u043f\u0430\u0441\u0438\u0431\u043e \u0437\u0430 \u043f\u043e\u0434\u0430\u0440\u043e\u043a",
    "\u0432\u0441\u0435 \u0447\u0435\u0442\u043a\u043e \u0431\u0435\u0437 \u043d\u0430\u0440\u0438\u043a\u043e\u0432",
    "\u0443\u0436\u0435 \u043d\u0435 \u043f\u0435\u0440\u0432\u044b\u0439 \u0440\u0430\u0437 \u0437\u0430\u043a\u0430\u0437\u044b\u0432\u0430\u044e \u0432\u0441\u0435 \u043d\u0430 \u0432\u044b\u0441\u043e\u0442\u0435",
    "\u043a\u0430\u0447\u0435\u0441\u0442\u0432\u043e \u043e\u0433\u043e\u043d\u044c \u0431\u0443\u0434\u0443 \u0431\u0440\u0430\u0442\u044c \u0435\u0449\u0451",
    "\u043f\u0440\u0438\u0448\u043b\u043e \u0431\u044b\u0441\u0442\u0440\u043e \u0432\u0441\u0451 \u043a\u0430\u043a \u043d\u0430\u0434\u043e",
]

# Existing product names for reviews
product_names = [
    "Afgan Cush VHQ", "Bruce Banner", "Lemon Haze", "Lizard King",
    "EXTAZY SKITTLES 260mg", "Crystal White Rain", "Bruce Banner \u043c\u0438\u043a\u0441\u044b"
]

# Delete old fake reviews
c.execute("DELETE FROM fake_reviews")

# Generate 15 reviews with dates spread across April-May 2026
start = datetime(2026, 4, 1)
end = datetime(2026, 5, 25)
reviews = []
for i in range(15):
    ts = start + (end - start) * random.random()
    date_str = ts.strftime("%Y-%m-%d %H:%M:%S")
    prod = random.choice(product_names)
    text = random.choice(review_texts)
    rating = random.choices([4, 5], weights=[20, 80])[0]
    reviews.append((prod, text, rating, date_str))

c.executemany(
    "INSERT INTO fake_reviews(product_name, review_text, rating, created_at) VALUES(?,?,?,?)",
    reviews
)

# ========== 2. PRODUCTS FOR EMPTY DISTRICTS ==========
# Get districts that have products and those that don't
districts_with = set(r[0] for r in c.execute("SELECT DISTINCT district_id FROM products").fetchall())
all_districts = [r for r in c.execute("SELECT id, city_id, name FROM districts").fetchall()]

# Product templates by assortment
# assortment_id -> [(title, min_price, max_price, unit)]
product_templates = {
    6: [  # \u0428\u0438\u0448\u043a\u0438
        ("Amnesia Haze", 2500, 6000, "\u0433"),
        ("OG Kush", 3000, 5500, "\u0433"),
        ("White Widow", 2800, 5000, "\u0433"),
        ("Blue Dream", 3200, 5800, "\u0433"),
        ("Lemon Haze", 2000, 10200, "\u0433"),
        ("Afgan Cush VHQ", 1950, 4140, "\u0433"),
        ("Bruce Banner", 2770, 5120, "\u0433"),
    ],
    7: [  # \u0413\u0440\u0438\u0431\u044b
        ("Golden Teacher", 4500, 7000, "\u0433"),
        ("Blue Meanie", 5000, 7500, "\u0433"),
        ("Lizard King", 6000, 6000, "\u0433"),
        ("Mckennaii", 4000, 6500, "\u0433"),
    ],
    8: [  # \u042d\u043a\u0441\u0442\u0430\u0437\u0438
        ("EXTAZY SKITTLES 260mg", 3999, 5999, "\u0448\u0442"),
        ("Punisher 300mg", 3500, 5500, "\u0448\u0442"),
        ("Roll Royce 280mg", 3800, 5800, "\u0448\u0442"),
    ],
    9: [  # \u041c\u0435\u0444\u0435\u0434\u0440\u043e\u043d
        ("Crystal White Rain", 2375, 4750, "\u0433"),
        ("\u041c\u0443\u0442\u043a\u0430 VHQ", 2000, 4500, "\u0433"),
        ("\u041a\u0440\u0438\u0441\u0442\u0430\u043b\u043b \u043f\u0440\u0435\u043c\u0438\u0443\u043c", 3000, 5500, "\u0433"),
    ],
}

# Pick ~40 districts that DON'T have products yet to add some
district_pool = [d for d in all_districts if d[0] not in districts_with]
random.shuffle(district_pool)
target_districts = district_pool[:50]  # add to 50 districts

added = 0
for dist_id, city_id, dist_name in target_districts:
    # Pick a random assortment
    ass_id = random.choice(list(product_templates.keys()))
    templates = product_templates[ass_id]
    # Pick 1-2 product templates for this district
    picks = random.sample(templates, min(random.randint(1, 2), len(templates)))
    for title, min_p, max_p, unit in picks:
        price = round(random.uniform(min_p, max_p) / 10) * 10  # round to 10
        qty_val = round(random.uniform(0.5, 5.0), 1)
        c.execute(
            "INSERT INTO products(district_id, assortment_id, title, price, qty_value, qty_unit) VALUES(?,?,?,?,?,?)",
            (dist_id, ass_id, title, price, qty_val, unit)
        )
        added += 1

conn.commit()

# Summary
print(f"Fake reviews added: {len(reviews)}")
print(f"Products added: {added}")
print(f"Total products now: {c.execute('SELECT COUNT(*) FROM products').fetchone()[0]}")
print(f"Districts with products: {c.execute('SELECT COUNT(DISTINCT district_id) FROM products').fetchone()[0]} out of {c.execute('SELECT COUNT(*) FROM districts').fetchone()[0]}")

conn.close()
print("Done!")
