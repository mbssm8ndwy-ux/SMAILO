"""Check DB schema and sample data."""
import sqlite3

conn = sqlite3.connect("shop.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== TABLES ===")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(f"  {r['name']}")

for table in ['cities', 'districts', 'assortments', 'products', 'fake_reviews', 'user_balances', 'balance_topups', 'orders']:
    print(f"\n=== {table} ===")
    # Schema
    for col in c.execute(f"PRAGMA table_info({table})"):
        print(f"  COL: {col}")
    # Sample data
    rows = c.execute(f"SELECT * FROM {table} LIMIT 5").fetchall()
    for r in rows:
        print(f"  DATA: {dict(r)}")
    # Count
    cnt = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  TOTAL: {cnt} rows")

conn.close()
