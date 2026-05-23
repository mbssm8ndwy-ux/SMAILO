import sqlite3
conn = sqlite3.connect("shop.db")
# Remove orders that ended up with NULL reviews (orphans from re-seeding)
cur = conn.execute("DELETE FROM orders WHERE id IN (38,39,40,41)")
print(f"Cleaned {cur.rowcount} orphan orders")
conn.commit()
conn.close()
