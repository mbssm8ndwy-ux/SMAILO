import sqlite3
conn = sqlite3.connect("shop.db")

# Products in Yalta (city_id=23)
rows = conn.execute("""
    SELECT d.name as district, p.id, p.title, p.price, a.name as assortment
    FROM districts d
    LEFT JOIN products p ON p.district_id = d.id
    LEFT JOIN assortments a ON a.id = p.assortment_id
    WHERE d.city_id = 23
    ORDER BY d.name, p.id
""").fetchall()

print("=== Yalta (city 23) products ===")
for r in rows:
    pid = r[1] if r[1] is not None else '-'
    print(f"{r[0]:<25} | id={str(pid):<3} | {str(r[2] or '-'):<25} | {str(r[3] or '')}rub | {str(r[4] or '')}")

# Check if Simferopol or Sevastopol exist
cities = conn.execute("SELECT id, name FROM cities ORDER BY id").fetchall()
print("\n=== All cities ===")
for r in cities:
    print(f"  {r[0]}: {r[1]}")

conn.close()
