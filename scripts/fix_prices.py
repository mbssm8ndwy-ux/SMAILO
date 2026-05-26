"""Fix all new product prices to be proportional to quantity and similar to original pricing."""
import sqlite3

conn = sqlite3.connect("shop.db")
c = conn.cursor()

# Base per-unit prices by product name
# These match the original DB pricing patterns
base_prices = {
    # Шишки (assortment 6) — ~2000-2500/г
    "Afgan Cush VHQ": 2100,
    "Amnesia Haze": 2200,
    "Blue Dream": 2300,
    "Bruce Banner": 2500,
    "Bruce Banner миксы": 2500,
    "Lemon Haze": 2050,
    "OG Kush": 2200,
    "White Widow": 2100,
    # Грибы (assortment 7) — ~1200-1500/г
    "Blue Meanie": 1500,
    "Golden Teacher": 1400,
    "Lizard King": 1200,  # original: 6000/5г = 1200
    "Mckennaii": 1400,
    # Экстази (assortment 8) — per piece
    "EXTAZY SKITTLES 260mg": 2000,
    "Punisher 300mg": 1800,
    "Roll Royce 280mg": 1900,
    # Мефедрон (assortment 9) — ~2000/г
    "Crystal White Rain": 2000,
    "Мутка VHQ": 1800,
    "Кристалл премиум": 2500,
}

fixed = 0
products = c.execute("SELECT id, title, price, qty_value, assortment_id FROM products WHERE id >= 53").fetchall()

for pid, title, old_price, qty, ass_id in products:
    base = base_prices.get(title)
    if not base:
        print(f"  SKIP: no base price for {title}")
        continue
    # Price = base per unit * quantity, rounded to 10
    new_price = round(base * qty / 10) * 10
    # Ensure minimum price
    if new_price < base:
        new_price = base
    c.execute("UPDATE products SET price = ? WHERE id = ?", (new_price, pid))
    fixed += 1
    # Show only if changed significantly
    if abs(new_price - old_price) > 20:
        print(f"  ID {pid}: {title} {qty}г — {old_price:.0f} -> {new_price:.0f} rub")

conn.commit()
print(f"\nFixed {fixed} products")

# Verify no inconsistencies remain
bad = c.execute("""
    SELECT a.id, a.title, a.district_id, a.qty_value, a.price,
           b.id, b.qty_value, b.price
    FROM products a
    JOIN products b ON a.district_id = b.district_id 
        AND a.assortment_id = b.assortment_id 
        AND a.title = b.title
        AND a.qty_value < b.qty_value
        AND a.price >= b.price
    WHERE a.id >= 53 OR b.id >= 53
""").fetchall()
if bad:
    print(f"\nWARNING: {len(bad)} inconsistencies remain!")
    for r in bad[:5]:
        print(f"  {r[1]}: {r[3]}g={r[4]}rub vs {r[6]}g={r[7]}rub")
else:
    print("No pricing inconsistencies found!")

conn.close()
