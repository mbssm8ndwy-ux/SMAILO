import sqlite3
conn = sqlite3.connect("shop.db")
cur = conn.execute("DELETE FROM orders WHERE id > 27 AND (review_text IS NULL OR TRIM(review_text) = '')")
print(f"Deleted {cur.rowcount} empty-review orders")
count = conn.execute("SELECT COUNT(*) FROM orders WHERE status='completed'").fetchone()[0]
print(f"Completed orders remaining: {count}")
conn.commit()
conn.close()
