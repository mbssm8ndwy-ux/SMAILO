import sqlite3, json

conn = sqlite3.connect("shop.db")

# Read known data
cities = dict(conn.execute("SELECT id, name FROM cities").fetchall())
districts = list(conn.execute("SELECT id, name, city_id FROM districts").fetchall())

# Create or get "Экстази" assortment
cur = conn.execute("SELECT id FROM assortments WHERE name = 'Экстази'")
existing = cur.fetchone()
if existing:
    ass_id = existing[0]
    print("Assortment Экстази already exists, id =", ass_id)
else:
    conn.execute("INSERT INTO assortments(name) VALUES ('Экстази')")
    ass_id = conn.execute("SELECT id FROM assortments WHERE name = 'Экстази'").fetchone()[0]
    print("Created assortment Экстази, id =", ass_id)

# Add products - MDMA кристаллы in key districts
products_to_add = [
    # (district_name, city_name, title, price, qty_val, qty_unit)
    ("Проспект Победы", "Симферополь", "MDMA кристаллы", 3500.0, 1.0, "г"),
    ("Площадь советская", "Симферополь", "MDMA кристаллы", 6500.0, 2.0, "г"),
    ("ул.Рабочая", "Ялта", "MDMA кристаллы", 3500.0, 1.0, "г"),
    ("ул.Рабочая", "Ялта", "MDMA кристаллы", 6500.0, 2.0, "г"),
    ("Центр / Набережная", "Ялта", "MDMA кристаллы", 3500.0, 1.0, "г"),
    ("Инкерман", "Севастополь", "MDMA кристаллы", 3500.0, 1.0, "г"),
    ("Центр (Ленина / Нахимова)", "Севастополь", "MDMA кристаллы", 6500.0, 2.0, "г"),
    ("ул.Севастополь", "Ялта", "MDMA кристаллы", 3500.0, 1.0, "г"),
    ("ул.Севастополь", "Ялта", "LSD марки", 2500.0, 1.0, "шт"),
    ("ул.Севастополь", "Ялта", "LSD марки", 4500.0, 2.0, "шт"),
    ("Проспект Победы", "Симферополь", "LSD марки", 2500.0, 1.0, "шт"),
    ("Гагаринский (ПОР / Омега)", "Севастополь", "LSD марки", 2500.0, 1.0, "шт"),
]

# Build district lookup
dist_map = {}
for d in districts:
    city_name = cities.get(d[2], "")
    dist_map[(d[1], city_name)] = d[0]

added = 0
for dname, cname, title, price, qty_val, qty_unit in products_to_add:
    did = dist_map.get((dname, cname))
    if not did:
        # Try finding by city
        cid = None
        for cid2, cn2 in cities.items():
            if cn2 == cname:
                cid = cid2
                break
        if cid:
            # Find district id
            drow = conn.execute("SELECT id FROM districts WHERE name = ? AND city_id = ?", (dname, cid)).fetchone()
            if drow:
                did = drow[0]
    if not did:
        print(f"  SKIP: {cname}/{dname} - not found")
        continue
    # Check if product exists
    exists = conn.execute(
        "SELECT id FROM products WHERE district_id = ? AND title = ? AND price = ?",
        (did, title, price)
    ).fetchone()
    if exists:
        print(f"  EXISTS: {cname}/{dname} - {title} {price}rub")
        continue
    conn.execute(
        "INSERT INTO products(district_id, assortment_id, title, price, qty_value, qty_unit) VALUES (?, ?, ?, ?, ?, ?)",
        (did, ass_id, title, price, qty_val, qty_unit),
    )
    added += 1
    print(f"  ADDED: {cname}/{dname} - {title} {price}rub")

conn.commit()
conn.close()
print(f"\nAdded {added} new products!")
