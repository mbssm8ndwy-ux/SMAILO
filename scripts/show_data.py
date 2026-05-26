"""Show existing cities, districts, products."""
import sqlite3
conn = sqlite3.connect("shop.db")
c = conn.cursor()

print("=== CITIES ===")
for r in c.execute("SELECT id, name FROM cities ORDER BY id"):
    print(f"  {r[0]}: {r[1]}")

print("\n=== DISTRICTS per city ===")
for r in c.execute("SELECT d.id, d.name, c.name FROM districts d JOIN cities c ON d.city_id=c.id ORDER BY c.id, d.id"):
    print(f"  {r[0]}: {r[1]} ({r[2]})")

print("\n=== PRODUCTS ===")
for r in c.execute("SELECT p.id, p.title, a.name, d.name, p.price FROM products p JOIN assortments a ON p.assortment_id=a.id JOIN districts d ON p.district_id=d.id ORDER BY a.id, d.id"):
    print(f"  {r[0]}: {r[1]} | {r[2]} | {r[3]} | {r[4]}")

conn.close()
