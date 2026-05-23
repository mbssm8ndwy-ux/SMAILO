import sqlite3

conn = sqlite3.connect("shop.db")

# Fix Sevastopol products: set correct gram quantities matching other cities
fixes = {
    # (city_id, district_name, product_title, price) -> (qty_value, qty_unit)
    # Балаклава
    ("Балаклава", "Lemon Haze", 4100.0): (2.0, "г"),
    ("Балаклава", "Afgan Cush VHQ", 2455.0): (1.0, "г"),
    # Гагаринский
    ("Гагаринский (ПОР / Омега)", "Bruce Banner", 4970.0): (2.0, "г"),
    ("Гагаринский (ПОР / Омега)", "Lemon Haze", 2100.0): (1.0, "г"),
    # Инкерман
    ("Инкерман", "Bruce Banner касание", 2770.0): (1.0, "г"),
    ("Инкерман", "Lemon Haze", 4155.0): (2.0, "г"),
    # Северная сторона
    ("Северная сторона", "Lizard King", 6000.0): (5.0, "г"),
    # Центр
    ("Центр (Ленина / Нахимова)", "Lemon Haze", 6000.0): (3.0, "г"),
    ("Центр (Ленина / Нахимова)", "Afgan Cush VHQ", 4140.0): (2.0, "г"),
}

updated = 0
conn.execute("BEGIN TRANSACTION")

for (dname, title, price), (qty_val, qty_unit) in fixes.items():
    cur = conn.execute("""
        UPDATE products SET qty_value = ?, qty_unit = ?
        WHERE id IN (
            SELECT p.id FROM products p
            JOIN districts d ON d.id = p.district_id
            WHERE d.name = ? AND p.title = ? AND p.price = ? AND d.city_id = 26
            LIMIT 1
        )
    """, (qty_val, qty_unit, dname, title, price))
    if cur.rowcount > 0:
        print(f"  Fixed: {dname} | {title} ({price}rub) -> {qty_val} {qty_unit}")
        updated += 1
    else:
        print(f"  NOT FOUND: {dname} | {title} ({price}rub)")

conn.commit()
conn.close()
print(f"\nUpdated {updated} products")
