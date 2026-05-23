import sqlite3, json

conn = sqlite3.connect("shop.db")

rows = conn.execute("""
    SELECT c.name as city, COUNT(*) as cnt
    FROM orders o
    JOIN products p ON p.id = o.product_id
    JOIN districts d ON d.id = p.district_id
    JOIN cities c ON c.id = d.city_id
    WHERE o.status = 'completed'
      AND o.created_at >= '2026-05-01'
      AND o.review_text IS NOT NULL
    GROUP BY c.name
    ORDER BY c.name
""").fetchall()

res = {}
for r in rows:
    res[r[0]] = r[1]

rows2 = conn.execute("""
    SELECT o.id, c.name as city, p.title, o.review_text, o.created_at
    FROM orders o
    JOIN products p ON p.id = o.product_id
    JOIN districts d ON d.id = p.district_id
    JOIN cities c ON c.id = d.city_id
    WHERE o.status='completed' AND o.review_text IS NOT NULL
    ORDER BY o.created_at DESC
    LIMIT 25
""").fetchall()

recent = []
for r in rows2:
    recent.append({"id": r[0], "city": r[1], "product": r[2], "review": r[3], "date": str(r[4])[:10] if r[4] else None})

rows3 = conn.execute("""
    SELECT d.name, p.title, p.price, a.name
    FROM districts d
    JOIN products p ON p.district_id = d.id
    JOIN assortments a ON a.id = p.assortment_id
    WHERE d.city_id = 26
    ORDER BY d.name
""").fetchall()

sevastopol = []
for r in rows3:
    sevastopol.append({"district": r[0], "product": r[1], "price": r[2], "assortment": r[3]})

total = conn.execute("SELECT COUNT(*) FROM orders WHERE status='completed' AND review_text IS NOT NULL").fetchone()[0]
conn.close()

out = {
    "may_reviews_by_city": res,
    "total_completed_with_reviews": total,
    "recent_reviews": recent,
    "sevastopol_products": sevastopol
}

with open(r"C:\Users\admin\AppData\Local\Temp\opencode\crimea_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("OK")
