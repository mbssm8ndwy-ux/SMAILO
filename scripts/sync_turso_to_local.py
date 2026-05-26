"""Sync Turso data back to local shop.db (preserve manual admin additions)."""
import sqlite3

conn = sqlite3.connect("shop.db")
c = conn.cursor()

# Add mephedrone assortment (id=9) if not present
c.execute("INSERT OR IGNORE INTO assortments(id, name) VALUES (9, 'Мефедрон')")

# Add mephedrone products (id=50,51,52) if not present
meph_products = [
    (50, 43, 9, 'Crystal White Rain', 2375.0, 1.0, 'г'),
    (51, 44, 9, 'Crystal White Rain', 4750.0, 1.0, 'г'),
    (52, 46, 9, 'Crystal White Rain', 4750.0, 1.0, 'г'),
]
for p in meph_products:
    c.execute(
        "INSERT OR IGNORE INTO products(id, district_id, assortment_id, title, price, qty_value, qty_unit) VALUES (?,?,?,?,?,?,?)",
        p
    )

conn.commit()

# Show results
c.row_factory = sqlite3.Row
print("Assortments:")
for r in c.execute("SELECT id, name FROM assortments"):
    print(f"  {r['id']}: {r['name']}")

print("\nMephedrone products:")
for r in c.execute("SELECT id, title, price FROM products WHERE assortment_id=9"):
    print(f"  {r['id']}: {r['title']} - {r['price']}")

conn.close()
