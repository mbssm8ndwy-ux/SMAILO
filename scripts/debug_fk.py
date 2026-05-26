"""Find the problematic product that causes FK error in Turso."""
import sqlite3

conn = sqlite3.connect("shop.db")
c = conn.cursor()

# Check which districts exist in Turso vs local
# List all districts referenced by products
print("=== Districts referenced by products (first 60) ===")
for r in c.execute("SELECT DISTINCT p.district_id, d.name, c.name FROM products p LEFT JOIN districts d ON p.district_id=d.id LEFT JOIN cities c ON d.city_id=c.id ORDER BY p.district_id"):
    print(f"  district_id={r[0]}: {r[1]} ({r[2]})")

print()
print("=== Check product IDs that exist in Turso ===")
print("First 53 products (IDs 20+): IDs 20-72")
print(f"54th product (row 53) would be ID 73")
print()

# Get product ID 73
r = c.execute("SELECT id, district_id, title, assortment_id FROM products WHERE id=73").fetchone()
if r:
    print(f"Product ID 73: district_id={r[1]}, title={r[2]}, assortment_id={r[3]}")
    # Check if that district exists
    d = c.execute("SELECT id, name FROM districts WHERE id=?", (r[1],)).fetchone()
    if d:
        print(f"District {d[0]}: {d[1]} - EXISTS locally")
    else:
        print(f"District {r[1]} does NOT exist locally!")
else:
    print("Product ID 73 not found - let me check all IDs:")
    ids = [r[0] for r in c.execute("SELECT id FROM products ORDER BY id LIMIT 80").fetchall()]
    print(f"Product IDs (20-99): {ids}")

# Check all product district_ids against existing districts
print()
print("=== All product district_ids ===")
all_district_ids = set(r[0] for r in c.execute("SELECT DISTINCT district_id FROM products").fetchall())
existing_district_ids = set(r[0] for r in c.execute("SELECT id FROM districts").fetchall())
missing = all_district_ids - existing_district_ids
if missing:
    print(f"MISSING districts in local DB: {missing}")
else:
    print("All district_ids exist locally")
    # Check if Turso might be missing some
    print(f"Local district IDs: {sorted(existing_district_ids)}")
    print(f"Used by products: {sorted(all_district_ids)}")

conn.close()
