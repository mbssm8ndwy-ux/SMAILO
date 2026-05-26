"""Fix product qty_values to whole numbers and sync to Turso."""
import sqlite3

conn = sqlite3.connect("shop.db")
c = conn.cursor()

# Show bad products
print("=== Products with fractional qty_value ===")
for r in c.execute("SELECT id, title, price, qty_value, qty_unit FROM products WHERE qty_value != CAST(qty_value AS INTEGER)"):
    print(f"  ID {r[0]}: {r[1]} - {r[3]}{r[4]}")

# Fix all qty_values to nearest integer (min 1)
c.execute("UPDATE products SET qty_value = MAX(1, ROUND(qty_value)) WHERE qty_value < 1 OR qty_value != CAST(qty_value AS INTEGER)")
conn.commit()

# Verify
remaining = c.execute("SELECT COUNT(*) FROM products WHERE qty_value != CAST(qty_value AS INTEGER)").fetchone()[0]
print(f"\nRemaining fractional: {remaining}")

# Show all new products (ID >= 53)
print("\n=== Fixed products (ID >= 53) ===")
for r in c.execute("SELECT id, title, price, qty_value, qty_unit FROM products WHERE id >= 53 ORDER BY id"):
    print(f"  ID {r[0]}: {r[1]} - {r[3]}{r[4]}")

conn.close()
print("Done!")
