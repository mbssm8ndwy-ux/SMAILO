"""Manually verify and fix Turso products qty: delete all, check is empty, re-insert."""
import json, os, sqlite3, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

HTTP_URL = config.TURSO_URL.replace("libsql://", "https://")
TOKEN = config.TURSO_AUTH_TOKEN

def _turso_type(v):
    if v is None: return {"type": "null"}
    if isinstance(v, bool): return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int): return {"type": "integer", "value": str(v)}
    if isinstance(v, float): return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}

def q(sql, params=None):
    args = [_turso_type(p) for p in (params or [])]
    body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}]}).encode()
    req = urllib.request.Request(HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    r = resp["results"][0]
    if r["type"] == "error": raise Exception(r["error"]["message"])
    return r["response"]["result"]["rows"]

def q_pipeline(statements):
    """Execute multiple SQL statements in a single pipeline request."""
    requests = []
    for sql, params in statements:
        if params is not None:
            args = [_turso_type(p) for p in params]
            requests.append({"type": "execute", "stmt": {"sql": sql, "args": args}})
        else:
            requests.append({"type": "execute", "stmt": {"sql": sql}})
    body = json.dumps({"requests": requests}).encode()
    req = urllib.request.Request(HTTP_URL + "/v2/pipeline", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read())
    for r in resp["results"]:
        if r["type"] == "error":
            raise Exception(r["error"]["message"])

# Step 1: Check local
print("Step 1: Checking local shop.db...")
conn = sqlite3.connect("shop.db")
c = conn.cursor()
bad_local = c.execute("SELECT COUNT(*) FROM products WHERE qty_value != CAST(qty_value AS INTEGER)").fetchone()[0]
total_local = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
print(f"  Local: {total_local} total, {bad_local} fractional")
if bad_local > 0:
    print("  ERROR: local still has fractional! Run fix_qty.py first.")
    sys.exit(1)

# Show local IDs 53-133 qty for verification
print("  Sample local products (IDs 129-133):")
for r in c.execute("SELECT id, qty_value FROM products WHERE id >= 129").fetchall():
    print(f"    ID {r[0]}: qty={r[1]}")
conn.close()

# Step 2: Check Turso before
print("\nStep 2: Checking Turso products before fix...")
turso_bad = q("SELECT COUNT(*) FROM products WHERE qty_value != CAST(qty_value AS INTEGER)")
turso_total = q("SELECT COUNT(*) FROM products")
print(f"  Turso: {turso_total} total, bad before fix: {turso_bad}")

# Step 3: Disable FK + delete all products in one pipeline
print("\nStep 3: Disabling FK and deleting all products...")
try:
    q_pipeline([
        ("PRAGMA foreign_keys = OFF", None),
        ("DELETE FROM products", None),
    ])
    print("  Products deleted!")
except Exception as e:
    print(f"  Error during delete: {e}")
    sys.exit(1)

# Verify empty
count_after_delete = q("SELECT COUNT(*) FROM products")
print(f"  Products after DELETE: {count_after_delete}")

# Step 4: Insert all products from local, one row at a time
print("\nStep 4: Inserting all products from local (row by row)...")
conn = sqlite3.connect("shop.db")
conn.row_factory = sqlite3.Row
all_products = conn.execute("SELECT * FROM products").fetchall()
conn2 = sqlite3.connect("shop.db")
pragma = conn2.execute("PRAGMA table_info(products)").fetchall()
col_names = [str(r[1]) for r in pragma]
conn2.close()

cols = ",".join(col_names)
ph = ",".join("?" for _ in col_names)
sql = f"INSERT INTO {('products')}({cols}) VALUES({ph})"

ok = 0
for i, row in enumerate(all_products):
    vals = tuple(row[c] for c in col_names)
    try:
        q(sql, vals)
        ok += 1
        if i > 0 and i % 25 == 0:
            print(f"  {i}/{len(all_products)}")
    except Exception as e:
        print(f"  Error at row {i} (ID {row['id']}): {e}")
        break

print(f"  Inserted: {ok}/{len(all_products)}")
conn.close()

# Step 5: Verify
print("\nStep 5: Verifying Turso products...")
turso_total2 = q("SELECT COUNT(*) FROM products")
turso_bad2 = q("SELECT COUNT(*) FROM products WHERE qty_value != CAST(qty_value AS INTEGER)")
print(f"  Turso after fix: {turso_total2}")
print(f"  Fractional in Turso: {turso_bad2}")

# Show last 5
print("\n  Last 5 products in Turso:")
for r in q("SELECT id, title, qty_value FROM products ORDER BY id DESC LIMIT 5"):
    print(f"    ID {r[0]['value']}: {r[1]['value']} - qty={r[2]['value']}")

# Re-enable FK in same pipeline as a select
q_pipeline([
    ("PRAGMA foreign_keys = ON", None),
])
print("\nDone!")
