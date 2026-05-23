import sqlite3

conn = sqlite3.connect("shop.db")
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT o.id, o.user_id, o.product_id, o.amount, o.status,
           o.review_text, o.review_rating, o.review_at, o.created_at,
           p.title as product_title
    FROM orders o
    LEFT JOIN products p ON p.id = o.product_id
    WHERE o.status = 'completed'
    ORDER BY o.created_at DESC
""").fetchall()

print(f"Total completed orders: {len(rows)}")
for r in rows:
    d = dict(r)
    rev = (d["review_text"] or "-")[:50]
    print(f'  #{d["id"]:>4} | {d["created_at"][:10] if d["created_at"] else "?"} | {str(d["product_title"] or "?"):<30} | {d["amount"]:>6}rub | rating={d["review_rating"]} | "{rev}"')
